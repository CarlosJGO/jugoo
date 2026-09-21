"""Tests for AIDYC client, provider, cache and status mapping."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from shell.models import TASK_STATUS_COMPLETED, TASK_STATUS_OVERDUE, TASK_STATUS_PENDING
from shell.servicios.tareas.aidyc_cache import (
    AidycCacheState,
    load_cache,
    mark_disconnected,
    save_cache,
    upsert_from_remote,
)
from shell.servicios.tareas.aidyc_client import (
    AidycAuthError,
    AidycClient,
    AidycClientConfig,
    AidycConflictError,
    AidycForbiddenError,
    AidycNetworkError,
    AidycNotFoundError,
    AidycTimeoutError,
)
from shell.servicios.tareas.aidyc_mapping import (
    aidyc_estado_to_jugoo,
    link_to_snapshot,
    parse_aidyc_local_id,
)
from shell.servicios.tareas.aidyc_provider import AidycTaskProvider
from shell.settings.schema import build_settings_catalog


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _client() -> AidycClient:
    return AidycClient(
        AidycClientConfig(
            base_url="https://aidyc.test",
            api_key="secret-key",
            padre_db="negocio",
            timeout_sec=2,
        )
    )


class AidycIntegrationTests(unittest.TestCase):
    def test_status_mapping(self) -> None:
        self.assertEqual(
            aidyc_estado_to_jugoo("completada", fecha_limite="2026-01-01"),
            TASK_STATUS_COMPLETED,
        )
        self.assertEqual(
            aidyc_estado_to_jugoo("pendiente", fecha_limite="2099-01-01", today=date(2026, 9, 20)),
            TASK_STATUS_PENDING,
        )
        self.assertEqual(
            aidyc_estado_to_jugoo("pendiente", fecha_limite="2020-01-01", today=date(2026, 9, 20)),
            TASK_STATUS_OVERDUE,
        )

    def test_parse_aidyc_local_id(self) -> None:
        self.assertEqual(parse_aidyc_local_id("aidyc:42"), 42)
        self.assertIsNone(parse_aidyc_local_id("local-1"))

    def test_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.json"
            state = upsert_from_remote(
                AidycCacheState(),
                [{"id": 1, "titulo": "A", "estado": "pendiente", "version": 2}],
                padre_db="negocio",
            )
            save_cache(path, state)
            loaded = load_cache(path)
            self.assertTrue(loaded.connected)
            self.assertEqual(loaded.items[0].aidyc_task_id, 1)
            self.assertEqual(loaded.items[0].last_known_version, 2)
            self.assertEqual(loaded.items[0].local_id, "aidyc:1")
            offline = mark_disconnected(loaded, "timeout")
            self.assertFalse(offline.connected)
            self.assertEqual(offline.last_error, "timeout")

    def test_link_to_snapshot(self) -> None:
        state = upsert_from_remote(
            AidycCacheState(),
            [{
                "id": 7,
                "titulo": "Factura",
                "descripcion": "Revisar",
                "estado": "pendiente",
                "prioridad": "alta",
                "categoria": "compras",
                "fecha_limite": "2099-12-01",
                "version": 1,
            }],
            padre_db="n1",
        )
        snap = link_to_snapshot(state.items[0], today=date(2026, 9, 20))
        self.assertEqual(snap.id, "aidyc:7")
        self.assertEqual(snap.title, "Factura")
        self.assertEqual(snap.status, TASK_STATUS_PENDING)
        self.assertEqual(snap.priority_id, "alta")

    def test_client_list_ok(self) -> None:
        fake = _FakeHTTPResponse({"ok": True, "tareas": [{"id": 1}], "total": 1, "has_more": False})
        with patch("urllib.request.urlopen", return_value=fake):
            data = _client().list_tareas(estado="pendiente")
        self.assertEqual(data["total"], 1)

    def test_client_http_errors(self) -> None:
        import urllib.error

        cases = [
            (401, AidycAuthError, "list"),
            (403, AidycForbiddenError, "list"),
            (404, AidycNotFoundError, "get"),
            (409, AidycConflictError, "put"),
        ]
        for code, exc, kind in cases:
            body = {
                "error": "x",
                "code": "version_conflict",
                "current_version": 5,
                "message": "conflicto",
            }
            err = urllib.error.HTTPError(
                "https://aidyc.test/x",
                code,
                "err",
                hdrs=None,
                fp=io.BytesIO(json.dumps(body).encode("utf-8")),
            )
            with patch("urllib.request.urlopen", side_effect=err):
                with self.assertRaises(exc) as raised:
                    if kind == "get":
                        _client().get_tarea(99)
                    elif kind == "put":
                        _client().update_tarea(1, {"titulo": "x", "expected_version": 1})
                    else:
                        _client().list_tareas()
                if code == 409:
                    self.assertEqual(raised.exception.current_version, 5)

    def test_client_timeout_and_network(self) -> None:
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(TimeoutError("timed out")),
        ):
            with self.assertRaises(AidycTimeoutError):
                _client().list_tareas()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(AidycNetworkError):
                _client().list_tareas()

    def test_provider_pull_complete_and_offline(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            client = MagicMock()
            client.configured = True
            client.list_tareas.return_value = {
                "ok": True,
                "padre_db": "negocio",
                "tareas": [{
                    "id": 3,
                    "titulo": "Cuadre",
                    "estado": "pendiente",
                    "version": 1,
                    "fecha_limite": "2099-01-01",
                }],
            }
            client.completar.return_value = {
                "ok": True,
                "tarea": {
                    "id": 3,
                    "titulo": "Cuadre",
                    "estado": "completada",
                    "version": 2,
                    "fecha_limite": "2099-01-01",
                },
            }
            provider = AidycTaskProvider(
                client,
                cache_path=Path(folder) / "c.json",
                padre_db="negocio",
            )
            self.assertTrue(provider.pull())
            self.assertTrue(provider.connected)
            self.assertEqual(len(provider.board()), 1)
            self.assertTrue(provider.complete("aidyc:3"))
            self.assertEqual(provider.get_link(3).snapshot["estado"], "completada")

            client.list_tareas.side_effect = AidycNetworkError("down")
            self.assertFalse(provider.pull())
            self.assertFalse(provider.connected)
            self.assertEqual(len(provider.records()), 1)

    def test_settings_catalog_has_aidyc_keys(self) -> None:
        keys = {item.key for item in build_settings_catalog()}
        self.assertIn("aidyc.enabled", keys)
        self.assertIn("aidyc.base_url", keys)
        self.assertIn("aidyc.api_key", keys)
        self.assertIn("aidyc.padre_db", keys)


if __name__ == "__main__":
    unittest.main()
