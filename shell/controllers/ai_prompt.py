"""Toggle the AI prompt layer and run asks off the GTK thread."""

from __future__ import annotations

import threading
from typing import Any

from gi.repository import GLib

from ..widgets.ia.prompt_overlay import AiPromptOverlay


class AiPromptController:
    """Owns the under-bar prompt entry and background llama calls."""

    def __init__(
        self,
        shell_window,
        notifications: Any,
        *,
        close_other_overlays: Any | None = None,
    ) -> None:
        self._notifications = notifications
        self._close_other_overlays = close_other_overlays
        self._busy = False
        self._overlay = AiPromptOverlay(
            shell_window,
            on_submit=self._on_submit,
        )

    def toggle(self) -> None:
        if self._overlay.is_open():
            self._overlay.close_prompt()
            return
        if self._close_other_overlays is not None:
            try:
                self._close_other_overlays()
            except Exception as error:  # noqa: BLE001
                print(f"shell: ai prompt close others: {error}", flush=True)
        self._overlay.open_prompt()

    def close(self) -> None:
        self._overlay.close_prompt()

    def _on_submit(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or self._busy:
            return
        self._busy = True
        thread = threading.Thread(
            target=self._run_ask,
            args=(cleaned,),
            name="jugoo-ai-ask",
            daemon=True,
        )
        thread.start()

    def _run_ask(self, text: str) -> None:
        from ..servicios.tareas.ask import run_user_ask

        try:
            run_user_ask(text, notifications=self._notifications)
        except Exception as error:  # noqa: BLE001
            print(f"shell: ai ask failed: {error}", flush=True)

            def _fallback() -> bool:
                try:
                    self._notifications.post_assistant(
                        body="No pude consultar al modelo ahora.",
                        meta="Error al preguntar",
                        source="fallback",
                        expire_timeout_ms=12_000,
                    )
                except Exception:
                    pass
                return False

            GLib.idle_add(_fallback)
        finally:
            GLib.idle_add(self._clear_busy)

    def _clear_busy(self) -> bool:
        self._busy = False
        return False
