"""HTTP client for AIDYC Jugoo integration API."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class AidycClientError(Exception):
    """Base error for AIDYC HTTP calls."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class AidycAuthError(AidycClientError):
    pass


class AidycForbiddenError(AidycClientError):
    pass


class AidycNotFoundError(AidycClientError):
    pass


class AidycConflictError(AidycClientError):
    def __init__(
        self,
        message: str,
        *,
        status: int = 409,
        payload: Any = None,
        current_version: int | None = None,
    ) -> None:
        super().__init__(message, status=status, payload=payload)
        self.current_version = current_version


class AidycTimeoutError(AidycClientError):
    pass


class AidycNetworkError(AidycClientError):
    pass


@dataclass(frozen=True)
class AidycClientConfig:
    base_url: str
    api_key: str
    padre_db: str = ""
    timeout_sec: float = 8.0


class AidycClient:
    """Thin HTTP client. No UI. No business rules beyond transport."""

    def __init__(self, config: AidycClientConfig) -> None:
        self._config = config

    @property
    def configured(self) -> bool:
        return bool(self._config.base_url.strip() and self._config.api_key.strip())

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/integracion/jugoo/v1/health", auth=False)

    def list_tareas(
        self,
        *,
        estado: str | None = None,
        prioridad: str | None = None,
        categoria: str | None = None,
        asignado: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"limit": str(limit)}
        if estado:
            params["estado"] = estado
        if prioridad:
            params["prioridad"] = prioridad
        if categoria:
            params["categoria"] = categoria
        if asignado:
            params["asignado"] = asignado
        if since:
            params["since"] = since
        if self._config.padre_db:
            params["padre_db"] = self._config.padre_db
        return self._request("GET", "/api/integracion/jugoo/v1/tareas", params=params)

    def get_tarea(self, tarea_id: int) -> dict[str, Any]:
        params = {}
        if self._config.padre_db:
            params["padre_db"] = self._config.padre_db
        return self._request(
            "GET",
            f"/api/integracion/jugoo/v1/tareas/{int(tarea_id)}",
            params=params or None,
        )

    def create_tarea(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        if self._config.padre_db and "padre_db" not in body:
            body["padre_db"] = self._config.padre_db
        return self._request("POST", "/api/integracion/jugoo/v1/tareas", body=body)

    def update_tarea(self, tarea_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        if self._config.padre_db and "padre_db" not in body:
            body["padre_db"] = self._config.padre_db
        return self._request(
            "PUT",
            f"/api/integracion/jugoo/v1/tareas/{int(tarea_id)}",
            body=body,
        )

    def completar(self, tarea_id: int) -> dict[str, Any]:
        return self._action(tarea_id, "completar")

    def reabrir(self, tarea_id: int) -> dict[str, Any]:
        return self._action(tarea_id, "reabrir")

    def posponer(self, tarea_id: int, *, minutos: int = 15) -> dict[str, Any]:
        body: dict[str, Any] = {"minutos": minutos}
        if self._config.padre_db:
            body["padre_db"] = self._config.padre_db
        return self._request(
            "POST",
            f"/api/integracion/jugoo/v1/tareas/{int(tarea_id)}/posponer",
            body=body,
        )

    def list_usuarios(self) -> dict[str, Any]:
        params = {}
        if self._config.padre_db:
            params["padre_db"] = self._config.padre_db
        return self._request(
            "GET",
            "/api/integracion/jugoo/v1/usuarios",
            params=params or None,
        )

    def _action(self, tarea_id: int, action: str) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self._config.padre_db:
            body["padre_db"] = self._config.padre_db
        return self._request(
            "POST",
            f"/api/integracion/jugoo/v1/tareas/{int(tarea_id)}/{action}",
            body=body,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        if auth and not self.configured:
            raise AidycAuthError("AIDYC no configurado (URL/API key)", status=401)

        base = self._config.base_url.rstrip("/")
        url = f"{base}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            headers["X-API-Key"] = self._config.api_key

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_sec) as response:
                raw = response.read().decode("utf-8")
                if not raw:
                    return {}
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    return {"data": payload}
                return payload
        except urllib.error.HTTPError as error:
            payload = _read_error_payload(error)
            message = _error_message(payload, default=str(error.reason))
            status = int(error.code)
            if status == 401:
                raise AidycAuthError(message, status=status, payload=payload) from error
            if status == 503 and "no configurada" in message.lower():
                raise AidycAuthError(message, status=status, payload=payload) from error
            if status == 403:
                raise AidycForbiddenError(message, status=status, payload=payload) from error
            if status == 404:
                raise AidycNotFoundError(message, status=status, payload=payload) from error
            if status == 409:
                current = None
                if isinstance(payload, dict):
                    current = payload.get("current_version")
                    try:
                        current = int(current) if current is not None else None
                    except (TypeError, ValueError):
                        current = None
                raise AidycConflictError(
                    message,
                    status=status,
                    payload=payload,
                    current_version=current,
                ) from error
            raise AidycClientError(message, status=status, payload=payload) from error
        except TimeoutError as error:
            raise AidycTimeoutError("Timeout al contactar AIDYC", status=None) from error
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, TimeoutError):
                raise AidycTimeoutError("Timeout al contactar AIDYC") from error
            raise AidycNetworkError(f"Red AIDYC: {reason}") from error
        except json.JSONDecodeError as error:
            raise AidycClientError(f"Respuesta JSON inválida: {error}") from error


def _read_error_payload(error: urllib.error.HTTPError) -> Any:
    try:
        raw = error.read().decode("utf-8")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _error_message(payload: Any, *, default: str) -> str:
    if isinstance(payload, dict):
        for key in ("error", "message", "code"):
            value = payload.get(key)
            if value:
                return str(value)
    return default
