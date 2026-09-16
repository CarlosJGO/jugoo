"""Lightweight background loop that reminds about local tasks."""

from __future__ import annotations

from datetime import datetime
import signal
import threading
import time
from typing import Callable

try:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
except (ImportError, ValueError):
    GLib = None  # type: ignore[assignment]

from ....eventbus import EventBus
from ....models import TASK_STATUS_OVERDUE
from ..proveedor import LocalTaskProvider, TaskProvider
from .config import WatcherConfig
from .contexto import ContextDetector, start_hyprland_context
from .sesion import acquire_watcher_lock, release_watcher_lock
from .estado import ReminderState, default_reminder_state
from .eventos import (
    KIND_AI_REMINDER,
    KIND_HEARTBEAT,
    KIND_REMINDER,
    event_name_for_kind,
)
from .ia import (
    LocalTextGenerator,
    attribute_messages_to_tasks,
    generate_reminder_text,
)
from .notificaciones import (
    ACTION_DONE,
    ACTION_OPEN,
    ACTION_SNOOZE_LONG,
    ACTION_SNOOZE_SHORT,
    DBusReminderNotifier,
)
from .politica import ActivitySnapshot, ReminderDecision, choose_reminder, cooldown_seconds
from .recursos import AiViability, ResourceMonitor

_REMINDER_STAGGER_SEC = 2


class TaskWatcher:
    def __init__(
        self,
        *,
        config: WatcherConfig | None = None,
        provider: TaskProvider | None = None,
        resource_monitor: ResourceMonitor | None = None,
        notifier: DBusReminderNotifier | None = None,
        generator: LocalTextGenerator | None = None,
        clock: Callable[[], datetime] | None = None,
        state: ReminderState | None = None,
    ) -> None:
        self._config = config if config is not None else WatcherConfig.from_shell()
        self._provider = provider if provider is not None else LocalTaskProvider()
        self._resources = resource_monitor if resource_monitor is not None else ResourceMonitor()
        self._notifier = notifier if notifier is not None else DBusReminderNotifier()
        self._generator = generator if generator is not None else LocalTextGenerator(self._config)
        self._clock = clock or datetime.now
        # Tests inject an in-memory ReminderState(); production persists to disk.
        self._state = state if state is not None else default_reminder_state()
        self._event_bus = EventBus(dispatch_on_main=False)
        self._hyprland = None
        self._context = ContextDetector(self._event_bus)
        self._loop: GLib.MainLoop | None = None
        self._tick_source_id = 0
        self._ai_lock = threading.Lock()
        self._ai_inflight = False
        self._ai_thread: threading.Thread | None = None
        self._notification_tasks: dict[int, str] = {}
        self._closed = False
        self._lock_fd: int | None = None

    def run(self) -> int:
        if GLib is None:
            print("Task watcher: GLib is required")
            return 1
        self._lock_fd = acquire_watcher_lock()
        if self._lock_fd is None:
            print("Task watcher: already running")
            return 0
        self._install_signals()
        self._notifier.start(self._on_notification_action)
        if self._config.enabled:
            try:
                self._hyprland, self._context = start_hyprland_context(
                    self._event_bus,
                    self._context,
                )
            except Exception as error:
                print(f"Task watcher: hyprland unavailable: {error}")
        interval = max(15, int(self._config.poll_interval_sec))
        self._tick_source_id = GLib.timeout_add_seconds(interval, self._on_tick)
        GLib.idle_add(self._on_startup_tick)
        self._loop = GLib.MainLoop()
        print("Task watcher: started")
        print("Task watcher: waiting for next tick")
        try:
            self._loop.run()
        finally:
            self.close()
        return 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        release_watcher_lock(self._lock_fd)
        self._lock_fd = None
        if self._tick_source_id and GLib is not None:
            GLib.source_remove(self._tick_source_id)
            self._tick_source_id = 0
        self._generator.close()
        self._notifier.close()
        if self._hyprland is not None:
            self._hyprland.close()
            self._hyprland = None
        self._event_bus.close()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.quit()
        self._loop = None

    def evaluate_once(
        self,
        *,
        activity: ActivitySnapshot | None = None,
        now: datetime | None = None,
    ) -> ReminderDecision:
        self._provider.reload_if_changed()
        when = now if now is not None else self._clock()
        board = self._provider.board(when.date())
        context = activity if activity is not None else self._context.snapshot(now=when.timestamp())
        return choose_reminder(board, self._state, context, now=when, config=self._config)

    def _on_startup_tick(self) -> bool:
        self._on_tick()
        return False

    def _on_tick(self) -> bool:
        if self._closed:
            return False
        print("Task watcher: tick")
        try:
            announced = self._tick()
            if not announced and self._config.enabled:
                self._announce(KIND_HEARTBEAT)
        except Exception as error:
            print(f"Task watcher: tick failed: {error}")
        print("Task watcher: waiting for next tick")
        return True

    def _tick(self) -> bool:
        if not self._config.enabled:
            return False
        with self._ai_lock:
            if self._ai_inflight:
                return True
        decision = self.evaluate_once()
        if not decision.should_notify or decision.snapshot is None:
            return False
        activity = self._context.snapshot()
        viability = AiViability(False, "skipped")
        if self._config.ai_enabled:
            viability = self._resources.viability(self._config)
        if viability.viable:
            with self._ai_lock:
                self._ai_inflight = True
            worker = threading.Thread(
                target=self._notify_with_optional_ai,
                args=(decision, activity, True),
                name="task-watcher-ai",
                daemon=True,
            )
            self._ai_thread = worker
            worker.start()
            return True
        self._notify_with_optional_ai(decision, activity, False)
        return True

    def _notify_with_optional_ai(
        self,
        decision: ReminderDecision,
        activity: ActivitySnapshot,
        use_ai: bool,
    ) -> None:
        try:
            now = self._clock()
            bodies, source = generate_reminder_text(
                decision,
                activity,
                now=now,
                config=self._config,
                generator=self._generator,
                use_ai=use_ai,
                state=self._state,
            )
            if use_ai and GLib is not None and self._loop is not None:
                GLib.idle_add(self._finish_reminder, decision, bodies, source)
                return
            self._finish_reminder(decision, bodies, source)
        except Exception as error:
            print(f"Task watcher: reminder failed: {error}")
            with self._ai_lock:
                self._ai_inflight = False

    def _finish_reminder(
        self,
        decision: ReminderDecision,
        bodies: list[str],
        source: str = "fallback",
    ) -> bool:
        try:
            self._emit_reminder_batch(decision, bodies, source)
        finally:
            with self._ai_lock:
                self._ai_inflight = False
        return False

    def _emit_reminder_batch(
        self,
        decision: ReminderDecision,
        bodies: list[str],
        source: str = "fallback",
    ) -> None:
        snapshot = decision.snapshot
        if snapshot is None:
            return
        cleaned = [str(body).strip() for body in bodies if str(body).strip()]
        if not cleaned:
            return
        cleaned = cleaned[:3]
        now = self._clock()
        now_ts = now.timestamp()
        attributed = attribute_messages_to_tasks(cleaned, decision)
        candidates = {
            item.id: item
            for item in (decision.candidates or ())
        }
        if snapshot.id not in candidates:
            candidates[snapshot.id] = snapshot
        # Always cooldown the primary pick so the next tick does not re-fire it.
        mentioned_ids = set(attributed) | {snapshot.id}
        for task_id in mentioned_ids:
            target = candidates.get(task_id)
            if target is None:
                continue
            message = attributed.get(task_id, cleaned[0] if task_id == snapshot.id else "")
            next_count = self._state.memory(task_id).reminder_count + 1
            wait = cooldown_seconds(next_count, self._config.reminder_cooldown_sec)
            self._state.mark_notified(
                task_id,
                target.period_key,
                now_ts,
                message=message,
                cooldown_sec=wait,
            )
        for index, body in enumerate(cleaned):
            delay = index * _REMINDER_STAGGER_SEC
            if delay <= 0 or GLib is None or self._loop is None:
                self._emit_one_notification(decision, body, source)
                continue
            GLib.timeout_add_seconds(
                delay,
                self._emit_one_notification_idle,
                decision,
                body,
                source,
            )

    def _emit_one_notification_idle(
        self,
        decision: ReminderDecision,
        body: str,
        source: str,
    ) -> bool:
        self._emit_one_notification(decision, body, source)
        return False

    def _emit_one_notification(
        self,
        decision: ReminderDecision,
        body: str,
        source: str = "fallback",
    ) -> None:
        snapshot = decision.snapshot
        if snapshot is None:
            return
        urgency = 2 if snapshot.status == TASK_STATUS_OVERDUE else 1
        notification_id = self._notifier.notify(
            summary="Jugoo",
            body=body,
            urgency=urgency,
            expire_timeout_ms=self._config.notification_timeout_ms,
            task_id=snapshot.id,
        )
        if notification_id is not None:
            self._notification_tasks[notification_id] = snapshot.id
        self._announce(KIND_AI_REMINDER if source == "ai" else KIND_REMINDER)
        print(f"Task watcher: reminded {snapshot.id} ({decision.reason})")

    def _announce(self, kind: str) -> None:
        self._event_bus.emit(event_name_for_kind(kind), kind)
        announcer = getattr(self._notifier, "announce", None)
        if callable(announcer):
            announcer(kind)

    def _on_notification_action(self, notification_id: int, action_key: str) -> None:
        task_id = self._notification_tasks.get(notification_id)
        if not task_id:
            return
        if action_key == ACTION_OPEN:
            self._notifier.open_tasks_panel()
            return
        if action_key == ACTION_DONE:
            if self._provider.complete(task_id):
                self._state.forget(task_id)
            return
        now = time.time()
        if action_key == ACTION_SNOOZE_SHORT:
            self._state.snooze(task_id, now + self._config.snooze_short_sec)
            return
        if action_key == ACTION_SNOOZE_LONG:
            self._state.snooze(task_id, now + self._config.snooze_long_sec)

    def _install_signals(self) -> None:
        def handle(_signum, _frame) -> None:
            if GLib is not None:
                GLib.idle_add(self.close)
            else:
                self.close()

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)


def run_task_watcher() -> int:
    watcher = TaskWatcher()
    return watcher.run()
