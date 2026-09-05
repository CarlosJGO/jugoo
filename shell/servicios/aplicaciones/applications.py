"""Owns the desktop catalog, pinned order, and application launch commands."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import threading
from typing import Callable, Sequence

from ...eventbus import EventBus
from ...models import (
    ApplicationsSnapshot,
    normalize_desktop_id,
    pin_application,
    unpin_application,
)
from ...runtime_paths import pinned_apps_path
from .desktop import desktop_directories_stamp, scan_desktop_applications, strip_exec_field_codes
from .store import load_pinned_ids, save_pinned_ids

APPLICATIONS_CHANGED = "applications_changed"
APP_ACTIVATE_REQUESTED = "app_activate_requested"
APP_PIN_TOGGLE_REQUESTED = "app_pin_toggle_requested"
LAUNCHER_TOGGLE_REQUESTED = "launcher_toggle_requested"

LaunchExecutor = Callable[[Sequence[str]], None]


class ApplicationsService:
    """Single source of truth for installed apps and the pinned dock order."""

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
        self._dir_stamp: tuple[tuple[str, int], ...] = ()
        self._event_bus.subscribe(APP_PIN_TOGGLE_REQUESTED, self._on_pin_toggle)

    @property
    def snapshot(self) -> ApplicationsSnapshot:
        with self._lock:
            return ApplicationsSnapshot(
                applications=self._applications,
                pinned_ids=self._pinned_ids,
            )

    def start(self) -> None:
        with self._lock:
            self._pinned_ids = load_pinned_ids(self._path)
            self._reload_catalog_locked()
        self._emit()

    def close(self) -> None:
        self._event_bus.unsubscribe(APP_PIN_TOGGLE_REQUESTED, self._on_pin_toggle)

    def refresh_catalog(self, *, force: bool = False) -> ApplicationsSnapshot:
        with self._lock:
            stamp = desktop_directories_stamp(self._directories)
            if not force and stamp == self._dir_stamp and self._applications:
                return ApplicationsSnapshot(
                    applications=self._applications,
                    pinned_ids=self._pinned_ids,
                )
            self._reload_catalog_locked(stamp)
            snapshot = ApplicationsSnapshot(
                applications=self._applications,
                pinned_ids=self._pinned_ids,
            )
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

    def launch(self, app_id: str) -> None:
        application = self.snapshot.app_by_id(app_id)
        if application is None:
            ident = normalize_desktop_id(app_id)
            if not ident:
                return
            _launch_desktop_id(ident, "", self._executor)
            return
        _launch_desktop_id(application.id, application.exec_cmd, self._executor)

    def _on_pin_toggle(self, app_id: object) -> None:
        if isinstance(app_id, str):
            self.toggle_pin(app_id)

    def _set_pinned(self, pinned_ids: tuple[str, ...]) -> None:
        with self._lock:
            if pinned_ids == self._pinned_ids:
                return
            self._pinned_ids = pinned_ids
            snapshot = ApplicationsSnapshot(
                applications=self._applications,
                pinned_ids=self._pinned_ids,
            )
        save_pinned_ids(self._path, pinned_ids)
        self._emit(snapshot)

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

    errors: list[str] = []
    for command in commands:
        try:
            executor(command)
            return
        except OSError as error:
            errors.append(str(error))
    if errors:
        print(f"shell: applications: could not launch {ident}: {errors[-1]}")


def _default_launch_executor(command: Sequence[str]) -> None:
    subprocess.Popen(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
