"""AIDYC-backed TaskProvider plus mutation helpers used by the Jugoo UI."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from ...models import TaskRecord, TaskSnapshot
from ...runtime_paths import aidyc_tasks_cache_path
from .aidyc_cache import (
    AidycCacheState,
    AidycTaskLink,
    load_cache,
    mark_disconnected,
    save_cache,
    upsert_from_remote,
)
from .aidyc_client import (
    AidycClient,
    AidycClientConfig,
    AidycClientError,
    AidycConflictError,
)
from .aidyc_mapping import link_to_record, link_to_snapshot, parse_aidyc_local_id


class AidycTaskProvider:
    """Caches AIDYC tasks locally and implements the TaskProvider contract."""

    def __init__(
        self,
        client: AidycClient,
        *,
        cache_path: Path | None = None,
        padre_db: str = "",
    ) -> None:
        self._client = client
        self._path = cache_path if cache_path is not None else aidyc_tasks_cache_path()
        self._padre_db = padre_db
        self._state = load_cache(self._path)
        self._mtime_ns = _path_mtime_ns(self._path)

    @property
    def client(self) -> AidycClient:
        return self._client

    @property
    def state(self) -> AidycCacheState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state.connected

    @property
    def last_error(self) -> str:
        return self._state.last_error

    @property
    def last_sync(self) -> str:
        return self._state.last_sync

    def records(self) -> tuple[TaskRecord, ...]:
        return tuple(link_to_record(item) for item in self._state.items)

    def board(self, today: date | None = None) -> tuple[TaskSnapshot, ...]:
        when = today if today is not None else date.today()
        return tuple(link_to_snapshot(item, today=when) for item in self._state.items)

    def links(self) -> tuple[AidycTaskLink, ...]:
        return self._state.items

    def get_link(self, task_id: str | int) -> AidycTaskLink | None:
        aidyc_id = parse_aidyc_local_id(str(task_id)) if not isinstance(task_id, int) else int(task_id)
        if aidyc_id is None and isinstance(task_id, str) and task_id.isdigit():
            aidyc_id = int(task_id)
        if aidyc_id is None:
            return None
        for item in self._state.items:
            if item.aidyc_task_id == aidyc_id:
                return item
        return None

    def reload_if_changed(self) -> bool:
        mtime_ns = _path_mtime_ns(self._path)
        if mtime_ns == self._mtime_ns:
            return False
        self._state = load_cache(self._path)
        self._mtime_ns = mtime_ns
        return True

    def complete(self, task_id: str, *, today: date | None = None) -> bool:
        del today  # AIDYC completion is absolute, not occurrence-based.
        aidyc_id = parse_aidyc_local_id(task_id)
        if aidyc_id is None:
            return False
        try:
            payload = self._client.completar(aidyc_id)
            tarea = payload.get("tarea") if isinstance(payload, dict) else None
            if isinstance(tarea, dict):
                self._merge_one(tarea)
            else:
                self.pull()
            return True
        except AidycClientError as error:
            self._mark_error(str(error))
            return False

    def reopen(self, task_id: str) -> bool:
        aidyc_id = parse_aidyc_local_id(task_id)
        if aidyc_id is None:
            return False
        try:
            payload = self._client.reabrir(aidyc_id)
            tarea = payload.get("tarea") if isinstance(payload, dict) else None
            if isinstance(tarea, dict):
                self._merge_one(tarea)
            else:
                self.pull()
            return True
        except AidycClientError as error:
            self._mark_error(str(error))
            return False

    def toggle(self, task_id: str) -> bool:
        link = self.get_link(task_id)
        if link is None:
            return False
        estado = str(link.snapshot.get("estado") or "")
        if estado == "completada":
            return self.reopen(task_id)
        return self.complete(task_id)

    def create(self, payload: dict[str, Any]) -> AidycTaskLink | None:
        try:
            response = self._client.create_tarea(payload)
            tarea = response.get("tarea") if isinstance(response, dict) else None
            if not isinstance(tarea, dict):
                return None
            self._merge_one(tarea)
            return self.get_link(int(tarea["id"]))
        except AidycClientError as error:
            self._mark_error(str(error))
            return None

    def update(
        self,
        task_id: str | int,
        payload: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> AidycTaskLink | None:
        aidyc_id = parse_aidyc_local_id(str(task_id)) if not isinstance(task_id, int) else int(task_id)
        if aidyc_id is None:
            return None
        body = dict(payload)
        if expected_version is not None:
            body["expected_version"] = expected_version
        elif "expected_version" not in body:
            link = self.get_link(aidyc_id)
            if link is not None:
                body["expected_version"] = link.last_known_version
        try:
            response = self._client.update_tarea(aidyc_id, body)
            tarea = response.get("tarea") if isinstance(response, dict) else None
            if not isinstance(tarea, dict):
                return None
            self._merge_one(tarea)
            return self.get_link(aidyc_id)
        except AidycConflictError:
            raise
        except AidycClientError as error:
            self._mark_error(str(error))
            return None

    def posponer(self, task_id: str | int, *, minutos: int = 15) -> bool:
        aidyc_id = parse_aidyc_local_id(str(task_id)) if not isinstance(task_id, int) else int(task_id)
        if aidyc_id is None:
            return False
        try:
            payload = self._client.posponer(aidyc_id, minutos=minutos)
            tarea = payload.get("tarea") if isinstance(payload, dict) else None
            if isinstance(tarea, dict):
                self._merge_one(tarea)
            return True
        except AidycClientError as error:
            self._mark_error(str(error))
            return False

    def list_usuarios(self) -> list[dict[str, Any]]:
        try:
            payload = self._client.list_usuarios()
            users = payload.get("usuarios") if isinstance(payload, dict) else None
            if isinstance(users, list):
                return [u for u in users if isinstance(u, dict)]
            return []
        except AidycClientError as error:
            self._mark_error(str(error))
            return []

    def pull(self, *, since: str | None = None, use_since: bool = True) -> bool:
        """Pull tasks from AIDYC into the local cache. Never raises."""
        if not self._client.configured:
            self._mark_error("AIDYC no configurado")
            return False
        query_since = since
        if use_since and not query_since and self._state.last_sync:
            # Full replace is safer for MVP correctness; since is available for callers.
            query_since = None
        try:
            payload = self._client.list_tareas(since=query_since, limit=500)
            tareas = payload.get("tareas") if isinstance(payload, dict) else None
            if not isinstance(tareas, list):
                self._mark_error("Respuesta de tareas inválida")
                return False
            padre = self._padre_db or str(payload.get("padre_db") or self._state.padre_db)
            self._state = upsert_from_remote(
                self._state,
                [t for t in tareas if isinstance(t, dict)],
                padre_db=padre,
                replace_all=query_since is None,
            )
            save_cache(self._path, self._state)
            self._mtime_ns = _path_mtime_ns(self._path)
            return True
        except AidycClientError as error:
            self._mark_error(str(error))
            return False
        except Exception as error:  # noqa: BLE001 — UI must never crash
            self._mark_error(str(error))
            return False

    def _merge_one(self, tarea: dict[str, Any]) -> None:
        padre = self._padre_db or self._state.padre_db
        self._state = upsert_from_remote(
            self._state,
            [tarea],
            padre_db=padre,
            replace_all=False,
        )
        save_cache(self._path, self._state)
        self._mtime_ns = _path_mtime_ns(self._path)

    def _mark_error(self, message: str) -> None:
        self._state = mark_disconnected(self._state, message)
        save_cache(self._path, self._state)
        self._mtime_ns = _path_mtime_ns(self._path)


def build_aidyc_provider_from_settings(
    get_setting: Callable[[str], Any],
    *,
    cache_path: Path | None = None,
) -> AidycTaskProvider | None:
    """Return a provider when enabled+configured; otherwise None."""
    enabled = bool(get_setting("aidyc.enabled"))
    base_url = str(get_setting("aidyc.base_url") or "").strip()
    api_key = str(get_setting("aidyc.api_key") or "").strip()
    padre_db = str(get_setting("aidyc.padre_db") or "").strip()
    try:
        timeout = float(get_setting("aidyc.timeout_sec") or 8)
    except (TypeError, ValueError):
        timeout = 8.0
    if not enabled or not base_url or not api_key:
        return None
    client = AidycClient(
        AidycClientConfig(
            base_url=base_url,
            api_key=api_key,
            padre_db=padre_db,
            timeout_sec=max(2.0, min(timeout, 60.0)),
        )
    )
    return AidycTaskProvider(client, cache_path=cache_path, padre_db=padre_db)


def _path_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0
