"""Thread-safe event delivery with an optional GTK-main-context dispatcher."""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Any, Callable, DefaultDict

try:
    from gi.repository import GLib
except (ImportError, ValueError):  # Keep non-GTK service tests usable.
    GLib = None  # type: ignore[assignment]


EventCallback = Callable[[Any], None]


class EventBus:
    """Thread-safe event fan-out.

    ``dispatch_on_main`` is enabled by the GTK application.  Its default is
    deliberately synchronous so pure service code and tests do not need a
    running GLib loop.  UI subscribers inherit the main-context dispatch;
    service subscribers that perform I/O must explicitly opt into direct
    delivery with ``on_main=False``.
    """

    def __init__(self, *, dispatch_on_main: bool = False) -> None:
        self._subscribers: DefaultDict[str, list[EventCallback]] = defaultdict(list)
        self._lock = RLock()
        self._dispatch_on_main = dispatch_on_main
        self._closed = False
        self._pending_source_ids: set[int] = set()
        self._callback_dispatch: dict[tuple[str, EventCallback], bool] = {}

    def subscribe(
        self,
        event_name: str,
        callback: EventCallback,
        *,
        on_main: bool | None = None,
    ) -> None:
        """Subscribe a callback, optionally overriding the bus dispatch mode."""
        if on_main is None:
            on_main = self._dispatch_on_main
        with self._lock:
            if self._closed:
                return
            self._callback_dispatch[(event_name, callback)] = on_main
            if callback not in self._subscribers[event_name]:
                self._subscribers[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback: EventCallback) -> None:
        with self._lock:
            callbacks = self._subscribers.get(event_name)
            if callbacks is None:
                return
            try:
                callbacks.remove(callback)
            except ValueError:
                return
            self._callback_dispatch.pop((event_name, callback), None)
            if not callbacks:
                del self._subscribers[event_name]

    def emit(self, event_name: str, payload: Any = None) -> None:
        # Copy before dispatch so subscribers can safely alter subscriptions in callbacks.
        with self._lock:
            if self._closed:
                return
            callbacks = tuple(self._subscribers.get(event_name, ()))
        for callback in callbacks:
            if self._callback_dispatch.get((event_name, callback), self._dispatch_on_main):
                self._dispatch_main(callback, payload)
            else:
                self._invoke(callback, payload)

    def close(self) -> None:
        """Stop delivery and cancel callbacks queued for the GTK main loop."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            source_ids = tuple(self._pending_source_ids)
            self._pending_source_ids.clear()
            self._subscribers.clear()
            self._callback_dispatch.clear()
        if GLib is not None:
            for source_id in source_ids:
                GLib.source_remove(source_id)

    def _dispatch_main(self, callback: EventCallback, payload: Any) -> None:
        if GLib is None:
            self._invoke(callback, payload)
            return

        source_id = 0

        def run() -> bool:
            with self._lock:
                self._pending_source_ids.discard(source_id)
                if self._closed:
                    return False
            self._invoke(callback, payload)
            return False

        source_id = GLib.idle_add(run)
        with self._lock:
            if self._closed:
                GLib.source_remove(source_id)
            else:
                self._pending_source_ids.add(source_id)

    @staticmethod
    def _invoke(callback: EventCallback, payload: Any) -> None:
        try:
            callback(payload)
        except Exception as error:
            # One unhealthy optional module must not take down a service thread
            # or GTK's main loop.
            print(f"shell: event callback failed: {error}")
