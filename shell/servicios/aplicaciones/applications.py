"""Owns the desktop catalog, pinned order, launcher favorites, and launching."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import threading
from typing import Callable, Sequence

from ...config import PINNED_APPS_VISIBLE_LIMIT
from ...eventbus import EventBus
from ...models import (
    ApplicationsSnapshot,
    DesktopApplication,
    move_pinned_application,
    new_instance_command,
    normalize_desktop_id,
    pin_application,
    send_pinned_to_overflow,
    unpin_application,
)
from ...runtime_paths import pinned_apps_path
from .desktop import desktop_directories_stamp, scan_desktop_applications, strip_exec_field_codes
from .store import load_application_prefs, save_application_prefs

APPLICATIONS_CHANGED = "applications_changed"
APP_ACTIVATE_REQUESTED = "app_activate_requested"
APP_NEW_INSTANCE_REQUESTED = "app_new_instance_requested"
APP_PIN_TOGGLE_REQUESTED = "app_pin_toggle_requested"
APP_PIN_REORDER_REQUESTED = "app_pin_reorder_requested"
APP_PIN_SEND_TO_OVERFLOW_REQUESTED = "app_pin_send_to_overflow_requested"
APP_FAVORITE_TOGGLE_REQUESTED = "app_favorite_toggle_requested"
LAUNCHER_TOGGLE_REQUESTED = "launcher_toggle_requested"

LaunchExecutor = Callable[[Sequence[str]], None]


class ApplicationsService:
    """Single source of truth for installed apps, dock pins, and launcher favorites."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        path: Path | None = None,
        directories: tuple[Path, ...] | None = None,
        executor: LaunchExecutor | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._path = path if path is not None else pinned_apps_path()
        self._directories = directories
        self._executor = executor or _default_launch_executor
        self._lock = threading.RLock()
        self._applications: tuple[DesktopApplication, ...] = ()
        self._pinned_ids: tuple[str, ...] = ()
        self._favorite_ids: tuple[str, ...] = ()
        self._dir_stamp: tuple[tuple[str, int], ...] = ()
        self._event_bus.subscribe(APP_PIN_TOGGLE_REQUESTED, self._on_pin_toggle)
        self._event_bus.subscribe(APP_PIN_REORDER_REQUESTED, self._on_pin_reorder)
        self._event_bus.subscribe(APP_PIN_SEND_TO_OVERFLOW_REQUESTED, self._on_send_to_overflow)
        self._event_bus.subscribe(APP_FAVORITE_TOGGLE_REQUESTED, self._on_favorite_toggle)
        self._event_bus.subscribe(APP_NEW_INSTANCE_REQUESTED, self._on_new_instance)

    @property
    def snapshot(self) -> ApplicationsSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def start(self) -> None:
        with self._lock:
            self._pinned_ids, self._favorite_ids = load_application_prefs(self._path)
            self._reload_catalog_locked()
        self._emit()

    def close(self) -> None:
        self._event_bus.unsubscribe(APP_PIN_TOGGLE_REQUESTED, self._on_pin_toggle)
        self._event_bus.unsubscribe(APP_PIN_REORDER_REQUESTED, self._on_pin_reorder)
        self._event_bus.unsubscribe(APP_PIN_SEND_TO_OVERFLOW_REQUESTED, self._on_send_to_overflow)
        self._event_bus.unsubscribe(APP_FAVORITE_TOGGLE_REQUESTED, self._on_favorite_toggle)
        self._event_bus.unsubscribe(APP_NEW_INSTANCE_REQUESTED, self._on_new_instance)

    def refresh_catalog(self, *, force: bool = False) -> ApplicationsSnapshot:
        with self._lock:
            stamp = desktop_directories_stamp(self._directories)
            if not force and stamp == self._dir_stamp and self._applications:
                return self._snapshot_locked()
            self._reload_catalog_locked(stamp)
            snapshot = self._snapshot_locked()
        self._emit(snapshot)
        return snapshot

    def pin(self, app_id: str) -> None:
        self._set_pinned(pin_application(self.snapshot.pinned_ids, app_id))

    def unpin(self, app_id: str) -> None:
        self._set_pinned(unpin_application(self.snapshot.pinned_ids, app_id))

    def toggle_pin(self, app_id: str) -> None:
        ident = normalize_desktop_id(app_id)
        if ident in self.snapshot.pinned_ids:
            self.unpin(ident)
        else:
            self.pin(ident)

    def reorder(self, source_id: str, target_id: str = "", *, at_index: int | None = None) -> None:
        self._set_pinned(
            move_pinned_application(
                self.snapshot.pinned_ids,
                source_id,
                target_id,
                at_index=at_index,
            )
        )

    def send_to_overflow(self, app_id: str) -> None:
        self._set_pinned(
            send_pinned_to_overflow(self.snapshot.pinned_ids, app_id, PINNED_APPS_VISIBLE_LIMIT)
        )

    def favorite(self, app_id: str) -> None:
        self._set_favorites(pin_application(self.snapshot.favorite_ids, app_id))

    def unfavorite(self, app_id: str) -> None:
        self._set_favorites(unpin_application(self.snapshot.favorite_ids, app_id))

    def toggle_favorite(self, app_id: str) -> None:
        ident = normalize_desktop_id(app_id)
        if ident in self.snapshot.favorite_ids:
            self.unfavorite(ident)
        else:
            self.favorite(ident)

    def launch(self, app_id: str) -> None:
        application = self.snapshot.app_by_id(app_id)
        if application is None:
            ident = normalize_desktop_id(app_id)
            if not ident:
                return
            _launch_desktop_id(ident, "", self._executor)
            return
        _launch_desktop_id(application.id, application.exec_cmd, self._executor)

    def launch_new_instance(self, app_id: str) -> None:
        application = self.snapshot.app_by_id(app_id)
        if application is None:
            self.launch(app_id)
            return
        command = new_instance_command(application)
        if command:
            _launch_shell_command(command, self._executor)
            return
        self.launch(app_id)

    def _on_pin_toggle(self, app_id: object) -> None:
        if isinstance(app_id, str):
            self.toggle_pin(app_id)

    def _on_pin_reorder(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            return
        at_index = payload.get("at_index")
        if isinstance(at_index, int):
            self.reorder(source_id, at_index=at_index)
            return
        target_id = payload.get("target_id")
        if isinstance(target_id, str) and target_id:
            self.reorder(source_id, target_id)

    def _on_send_to_overflow(self, app_id: object) -> None:
        if isinstance(app_id, str):
            self.send_to_overflow(app_id)

    def _on_favorite_toggle(self, app_id: object) -> None:
        if isinstance(app_id, str):
            self.toggle_favorite(app_id)

    def _on_new_instance(self, app_id: object) -> None:
        if isinstance(app_id, str):
            self.launch_new_instance(app_id)

    def _set_pinned(self, pinned_ids: tuple[str, ...]) -> None:
        with self._lock:
            if pinned_ids == self._pinned_ids:
                return
            self._pinned_ids = pinned_ids
            snapshot = self._snapshot_locked()
        self._persist()
        self._emit(snapshot)

    def _set_favorites(self, favorite_ids: tuple[str, ...]) -> None:
        with self._lock:
            if favorite_ids == self._favorite_ids:
                return
            self._favorite_ids = favorite_ids
            snapshot = self._snapshot_locked()
        self._persist()
        self._emit(snapshot)

    def _persist(self) -> None:
        with self._lock:
            pinned = self._pinned_ids
            favorites = self._favorite_ids
        save_application_prefs(self._path, pinned, favorites)

    def _snapshot_locked(self) -> ApplicationsSnapshot:
        return ApplicationsSnapshot(
            applications=self._applications,
            pinned_ids=self._pinned_ids,
            favorite_ids=self._favorite_ids,
        )

    def _reload_catalog_locked(self, stamp: tuple[tuple[str, int], ...] | None = None) -> None:
        self._applications = scan_desktop_applications(self._directories)
        self._dir_stamp = stamp if stamp is not None else desktop_directories_stamp(self._directories)

    def _emit(self, snapshot: ApplicationsSnapshot | None = None) -> None:
        self._event_bus.emit(APPLICATIONS_CHANGED, snapshot if snapshot is not None else self.snapshot)


def _launch_desktop_id(app_id: str, exec_cmd: str, executor: LaunchExecutor) -> None:
    ident = normalize_desktop_id(app_id)
    commands: list[tuple[str, ...]] = []
    if shutil.which("uwsm"):
        commands.append(("uwsm", "app", "--", "gtk-launch", ident))
    commands.append(("gtk-launch", ident))
    cleaned = strip_exec_field_codes(exec_cmd)
    if cleaned:
        commands.append(("/bin/sh", "-c", cleaned))
    _try_commands(commands, ident, executor)


def _launch_shell_command(command: str, executor: LaunchExecutor) -> None:
    commands: list[tuple[str, ...]] = []
    if shutil.which("uwsm"):
        commands.append(("uwsm", "app", "--", "/bin/sh", "-c", command))
    commands.append(("/bin/sh", "-c", command))
    _try_commands(commands, command, executor)


def _try_commands(commands: Sequence[tuple[str, ...]], label: str, executor: LaunchExecutor) -> None:
    errors: list[str] = []
    for command in commands:
        try:
            executor(command)
            return
        except OSError as error:
            errors.append(str(error))
    if errors:
        print(f"shell: applications: could not launch {label}: {errors[-1]}")


def _default_launch_executor(command: Sequence[str]) -> None:
    subprocess.Popen(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
