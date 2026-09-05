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
from .estado import ReminderState
from .eventos import (
    KIND_AI_REMINDER,
    KIND_HEARTBEAT,
    KIND_REMINDER,
    event_name_for_kind,
)
from .ia import LocalTextGenerator, generate_reminder_text
from .notificaciones import (
    ACTION_DONE,
    ACTION_OPEN,
    ACTION_SNOOZE_LONG,
    ACTION_SNOOZE_SHORT,
    DBusReminderNotifier,
)
from .politica import ActivitySnapshot, ReminderDecision, choose_reminder
from .recursos import AiViability, ResourceMonitor


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
    ) -> None:
        self._config = config if config is not None else WatcherConfig.from_shell()
        self._provider = provider if provider is not None else LocalTaskProvider()
        self._resources = resource_monitor if resource_monitor is not None else ResourceMonitor()
        self._notifier = notifier if notifier is not None else DBusReminderNotifier()
        self._generator = generator if generator is not None else LocalTextGenerator(self._config)
        self._clock = clock or datetime.now
        self._state = ReminderState()
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

    def run(self) -> int:
        if GLib is None:
            print("shell: task-watcher: GLib is required")
            return 1
        self._install_signals()
        self._notifier.start(self._on_notification_action)
        if self._config.enabled:
            try:
                self._hyprland, self._context = start_hyprland_context(
                    self._event_bus,
                    self._context,
                )
            except Exception as error:
                print(f"shell: task-watcher: hyprland unavailable: {error}")
        interval = max(15, int(self._config.poll_interval_sec))
        self._tick_source_id = GLib.timeout_add_seconds(interval, self._on_tick)
        GLib.idle_add(self._on_tick)
        self._loop = GLib.MainLoop()
        print("shell: task-watcher: running")
        try:
            self._loop.run()
        finally:
            self.close()
        return 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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

    def _on_tick(self) -> bool:
        if self._closed:
            return False
        try:
            announced = self._tick()
            if not announced and self._config.enabled:
                self._announce(KIND_HEARTBEAT)
        except Exception as error:
            print(f"shell: task-watcher: tick failed: {error}")
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
            body, source = generate_reminder_text(
                decision,
                activity,
                now=now,
                config=self._config,
                generator=self._generator,
                use_ai=use_ai,
            )
            if use_ai and GLib is not None and self._loop is not None:
                GLib.idle_add(self._finish_reminder, decision, body, source)
                return
            self._finish_reminder(decision, body, source)
        except Exception as error:
            print(f"shell: task-watcher: reminder failed: {error}")
            with self._ai_lock:
                self._ai_inflight = False

    def _finish_reminder(self, decision: ReminderDecision, body: str, source: str = "fallback") -> bool:
        try:
            self._emit_reminder(decision, body, source)
        finally:
            with self._ai_lock:
                self._ai_inflight = False
        return False

    def _emit_reminder(self, decision: ReminderDecision, body: str, source: str = "fallback") -> None:
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
        self._state.mark_notified(snapshot.id, snapshot.period_key, self._clock().timestamp())
        if notification_id is not None:
            self._notification_tasks[notification_id] = snapshot.id
        self._announce(KIND_AI_REMINDER if source == "ai" else KIND_REMINDER)
        print(f"shell: task-watcher: reminded {snapshot.id} ({decision.reason})")

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
