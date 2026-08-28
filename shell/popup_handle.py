"""Lazy popup handles and Wayland-safe outside-click dismissal."""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .config import POPUP_OUTSIDE_DISMISS_GRACE_MS
from .models import ActiveWindow
from .servicios.escritorio.hyprland import ACTIVE_WINDOW_CHANGED

T = TypeVar("T", bound=Gtk.Window)

_POPUP_FADE_TICK_MS = 16
_POPUP_FADE_STEP = 0.20


def _cancel_popup_fade(window: Gtk.Window) -> None:
    source_id = getattr(window, "_shell_fade_source_id", 0)
    if source_id:
        GLib.source_remove(source_id)
        setattr(window, "_shell_fade_source_id", 0)


def _fade_in_tick(window: Gtk.Window) -> bool:
    next_opacity = min(1.0, window.get_opacity() + _POPUP_FADE_STEP)
    window.set_opacity(next_opacity)
    if next_opacity >= 1.0:
        setattr(window, "_shell_fade_source_id", 0)
        return False
    return True


def _fade_out_tick(window: Gtk.Window) -> bool:
    next_opacity = max(0.0, window.get_opacity() - _POPUP_FADE_STEP)
    window.set_opacity(next_opacity)
    if next_opacity <= 0.02:
        setattr(window, "_shell_fade_source_id", 0)
        window.hide()
        window.set_opacity(1.0)
        return False
    return True


def present_popup(window: Gtk.Window) -> None:
    """Show a popup with a short opacity fade, without changing its position.

    Interactive popups may accept keyboard focus once on map. If the window is
    already visible, do not call ``present()`` again so refreshes do not steal
    Hyprland focus from the previously active application.
    """
    _cancel_popup_fade(window)
    if window.get_visible():
        window.set_opacity(1.0)
        return
    window.set_opacity(0.0)
    window.show_all()
    window.present()
    source_id = GLib.timeout_add(_POPUP_FADE_TICK_MS, _fade_in_tick, window)
    setattr(window, "_shell_fade_source_id", source_id)


def hide_popup(window: Gtk.Window) -> None:
    """Hide a popup with a short opacity fade."""
    _cancel_popup_fade(window)
    if not window.get_visible():
        window.hide()
        window.set_opacity(1.0)
        return
    source_id = GLib.timeout_add(_POPUP_FADE_TICK_MS, _fade_out_tick, window)
    setattr(window, "_shell_fade_source_id", source_id)


def pointer_inside_widget(widget: Gtk.Widget) -> bool:
    if not widget.get_mapped():
        return False

    window = widget.get_window()
    if window is None:
        return False

    pointer = widget.get_display().get_default_seat().get_pointer()
    if pointer is None:
        return False

    _, root_x, root_y = pointer.get_position()
    origin = window.get_origin()
    if len(origin) == 3:
        _, widget_x, widget_y = origin
    else:
        widget_x, widget_y = origin

    allocation = widget.get_allocation()
    return (
        widget_x <= root_x <= widget_x + allocation.width
        and widget_y <= root_y <= widget_y + allocation.height
    )


def pointer_inside_window(window: Gtk.Window) -> bool:
    return pointer_inside_widget(window)


class PopupHandle(Generic[T]):
    """Create-on-demand popup that clears its reference on Gtk destroy."""

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._window: T | None = None

    @property
    def maybe(self) -> T | None:
        return self._window

    def get(self) -> T:
        if self._window is None:
            window = self._factory()
            window.connect("destroy", self._on_destroy)
            self._window = window
        return self._window

    def _on_destroy(self, _window: Gtk.Window) -> None:
        self._window = None

    def hide(self) -> None:
        if self._window is not None:
            self._window.hide()

    def is_visible(self) -> bool:
        if self._window is None:
            return False
        return self._window.get_visible()


def _is_pointer_crossing_leave_surface(event: object | None) -> bool:
    """Ignore child-widget and grab crossings; they are not a real leave."""
    if event is None:
        return True
    mode = getattr(event, "mode", None)
    if mode in (Gdk.CrossingMode.GRAB, Gdk.CrossingMode.UNGRAB):
        return False
    detail = getattr(event, "detail", None)
    if detail == Gdk.NotifyType.INFERIOR:
        return False
    return True


class PopupOutsideDismiss:
    """Hide popups when the pointer leaves, independent of keyboard focus.

    Pointer hover owns the dismiss timer:

    * ENTER popup/anchor → cancel any pending dismiss
    * LEAVE popup/anchor → start ``POPUP_OUTSIDE_DISMISS_GRACE_MS``
    * timer fires → close only if the pointer is still outside

    Focus and Hyprland active-window changes never cancel a running timer and
    never replace one. They only start a timer if the pointer is already
    outside and no timer is pending (missed leave-notify on Wayland).
    Bar clicks outside the popup still close immediately.
    """

    _INSTALL_GRACE_USEC = 200_000

    def __init__(self) -> None:
        self._popup: Gtk.Window | None = None
        self._shell_window: Gtk.Window | None = None
        self._anchors: tuple[Gtk.Widget, ...] = ()
        self._on_dismiss: Callable[[], None] | None = None
        self._popup_title: str = ""
        self._shell_press_id: int | None = None
        self._focus_out_id: int | None = None
        self._popup_enter_id: int | None = None
        self._popup_leave_id: int | None = None
        self._anchor_enter_ids: list[tuple[Gtk.Widget, int]] = []
        self._anchor_leave_ids: list[tuple[Gtk.Widget, int]] = []
        self._install_grace_until: int = 0
        self._deferred_dismiss_source_id: int = 0
        self._dismiss_generation: int = 0
        self._event_bus = None
        self._active_window_handler = None
        self._extra_windows: tuple[Gtk.Window, ...] = ()
        self._extra_focus_out_ids: list[int] = []
        self._extra_enter_ids: list[int] = []
        self._extra_leave_ids: list[int] = []

    def install(
        self,
        popup: Gtk.Window,
        shell_window: Gtk.Window,
        anchor_widgets: tuple[Gtk.Widget, ...],
        on_dismiss: Callable[[], None],
        event_bus,
        extra_windows: tuple[Gtk.Window, ...] = (),
    ) -> None:
        self.uninstall()
        self._dismiss_generation += 1
        self._popup = popup
        self._shell_window = shell_window
        self._anchors = anchor_widgets
        self._on_dismiss = on_dismiss
        self._popup_title = popup.get_title() or ""
        self._install_grace_until = GLib.get_monotonic_time() + self._INSTALL_GRACE_USEC
        self._event_bus = event_bus
        self._extra_windows = extra_windows
        self._shell_press_id = shell_window.connect(
            "button-press-event",
            self._on_shell_button_press,
        )
        self._focus_out_id = popup.connect("focus-out-event", self._on_focus_out)
        self._popup_enter_id = popup.connect("enter-notify-event", self._on_pointer_enter)
        self._popup_leave_id = popup.connect("leave-notify-event", self._on_pointer_leave)
        for anchor in anchor_widgets:
            enter_id = anchor.connect("enter-notify-event", self._on_pointer_enter)
            leave_id = anchor.connect("leave-notify-event", self._on_pointer_leave)
            self._anchor_enter_ids.append((anchor, enter_id))
            self._anchor_leave_ids.append((anchor, leave_id))
        for extra in extra_windows:
            focus_id = extra.connect("focus-out-event", self._on_focus_out)
            self._extra_focus_out_ids.append(focus_id)
            self._extra_enter_ids.append(
                extra.connect("enter-notify-event", self._on_pointer_enter)
            )
            self._extra_leave_ids.append(
                extra.connect("leave-notify-event", self._on_pointer_leave)
            )
        self._active_window_handler = self._on_active_window_changed
        event_bus.subscribe(ACTIVE_WINDOW_CHANGED, self._active_window_handler)

    def uninstall(self) -> None:
        self._cancel_deferred_dismiss()
        self._dismiss_generation += 1
        if self._event_bus is not None and self._active_window_handler is not None:
            self._event_bus.unsubscribe(
                ACTIVE_WINDOW_CHANGED,
                self._active_window_handler,
            )
        if self._shell_window is not None and self._shell_press_id is not None:
            self._shell_window.disconnect(self._shell_press_id)
        if self._popup is not None:
            if self._focus_out_id is not None:
                self._popup.disconnect(self._focus_out_id)
            if self._popup_enter_id is not None:
                self._popup.disconnect(self._popup_enter_id)
            if self._popup_leave_id is not None:
                self._popup.disconnect(self._popup_leave_id)
        for anchor, handler_id in self._anchor_enter_ids:
            anchor.disconnect(handler_id)
        for anchor, handler_id in self._anchor_leave_ids:
            anchor.disconnect(handler_id)
        for extra, handler_id in zip(self._extra_windows, self._extra_focus_out_ids):
            if extra and handler_id:
                extra.disconnect(handler_id)
        for extra, handler_id in zip(self._extra_windows, self._extra_enter_ids):
            if extra and handler_id:
                extra.disconnect(handler_id)
        for extra, handler_id in zip(self._extra_windows, self._extra_leave_ids):
            if extra and handler_id:
                extra.disconnect(handler_id)
        self._popup = None
        self._shell_window = None
        self._anchors = ()
        self._on_dismiss = None
        self._popup_title = ""
        self._shell_press_id = None
        self._focus_out_id = None
        self._popup_enter_id = None
        self._popup_leave_id = None
        self._anchor_enter_ids = []
        self._anchor_leave_ids = []
        self._extra_windows = ()
        self._extra_focus_out_ids = []
        self._extra_enter_ids = []
        self._extra_leave_ids = []
        self._install_grace_until = 0
        self._event_bus = None
        self._active_window_handler = None

    def set_extra_windows(self, extra_windows: tuple[Gtk.Window, ...]) -> None:
        previous_windows = self._extra_windows
        previous_handlers = self._extra_focus_out_ids
        previous_enter_handlers = self._extra_enter_ids
        previous_leave_handlers = self._extra_leave_ids
        for extra, handler_id in zip(previous_windows, previous_handlers):
            if extra and handler_id:
                extra.disconnect(handler_id)
        for extra, handler_id in zip(previous_windows, previous_enter_handlers):
            if extra and handler_id:
                extra.disconnect(handler_id)
        for extra, handler_id in zip(previous_windows, previous_leave_handlers):
            if extra and handler_id:
                extra.disconnect(handler_id)
        self._extra_windows = extra_windows
        self._extra_focus_out_ids = []
        self._extra_enter_ids = []
        self._extra_leave_ids = []
        for extra in extra_windows:
            focus_id = extra.connect("focus-out-event", self._on_focus_out)
            self._extra_focus_out_ids.append(focus_id)
            self._extra_enter_ids.append(
                extra.connect("enter-notify-event", self._on_pointer_enter)
            )
            self._extra_leave_ids.append(
                extra.connect("leave-notify-event", self._on_pointer_leave)
            )

    def _cancel_deferred_dismiss(self) -> None:
        if self._deferred_dismiss_source_id:
            GLib.source_remove(self._deferred_dismiss_source_id)
            self._deferred_dismiss_source_id = 0

    def _schedule_deferred_dismiss(self, *, restart: bool = False) -> None:
        if self._deferred_dismiss_source_id and not restart:
            return
        self._cancel_deferred_dismiss()
        generation = self._dismiss_generation
        self._deferred_dismiss_source_id = GLib.timeout_add(
            POPUP_OUTSIDE_DISMISS_GRACE_MS,
            self._on_deferred_dismiss,
            generation,
        )

    def _pointer_over_popup_or_anchor(self) -> bool:
        popup = self._popup
        if popup is not None and pointer_inside_widget(popup):
            return True
        for extra in self._extra_windows:
            if pointer_inside_window(extra):
                return True
        for anchor in self._anchors:
            if pointer_inside_widget(anchor):
                return True
        return False

    def _on_shell_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button not in (1, 3):
            return False
        popup = self._popup
        if popup is None or not popup.get_visible():
            return False
        if pointer_inside_widget(popup):
            return False
        for anchor in self._anchors:
            if pointer_inside_widget(anchor):
                return False
        self._cancel_deferred_dismiss()
        GLib.idle_add(self._dismiss)
        return False

    def _on_pointer_enter(self, _widget: Gtk.Widget, event: Gdk.EventCrossing | None) -> bool:
        if not _is_pointer_crossing_leave_surface(event):
            return False
        self._cancel_deferred_dismiss()
        return False

    def _on_pointer_leave(self, _widget: Gtk.Widget, event: Gdk.EventCrossing | None) -> bool:
        if not _is_pointer_crossing_leave_surface(event):
            return False
        if GLib.get_monotonic_time() < self._install_grace_until:
            return False
        self._schedule_deferred_dismiss(restart=False)
        return False

    def _on_focus_out(self, _widget: Gtk.Widget, _event: Gdk.EventFocus) -> bool:
        if GLib.get_monotonic_time() < self._install_grace_until:
            return False
        if self._pointer_over_popup_or_anchor():
            return False
        self._schedule_deferred_dismiss(restart=False)
        return False

    def _any_window_has_focus(self) -> bool:
        if self._popup is not None and self._popup.get_visible() and self._popup.has_focus():
            return True
        for extra in self._extra_windows:
            if extra.get_visible() and extra.has_focus():
                return True
        return False

    def _on_active_window_changed(self, active_window: ActiveWindow) -> None:
        if GLib.get_monotonic_time() < self._install_grace_until:
            return
        if self._any_window_has_focus():
            return
        if self._pointer_over_popup_or_anchor():
            return
        self._schedule_deferred_dismiss(restart=False)

    def _on_deferred_dismiss(self, generation: int) -> bool:
        if generation != self._dismiss_generation:
            return False
        self._deferred_dismiss_source_id = 0
        self._dismiss_unless_pointer_inside()
        return False

    def _dismiss_unless_pointer_inside(self) -> bool:
        if self._pointer_over_popup_or_anchor() or self._any_window_has_focus():
            return False
        self._dismiss()
        return False

    def _dismiss(self) -> bool:
        on_dismiss = self._on_dismiss
        self.uninstall()
        if on_dismiss is not None:
            on_dismiss()
        return False
