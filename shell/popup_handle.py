"""Lazy popup handles and Wayland-safe outside-click dismissal."""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .ui.theme import active_theme

T = TypeVar("T", bound=Gtk.Window)

_POPUP_FADE_TICK_MS = 16

# Shell bar press probes registered while a popup is open.
_SHELL_PROBES_ATTR = "_jugoo_popup_outside_probes"
_event_handler_installed = False

def _popup_fade_step() -> float:
    theme = active_theme()
    if theme is None:
        return 0.20
    if not theme.animation.enabled or theme.animation.duration <= _POPUP_FADE_TICK_MS:
        return 1.0
    return min(1.0, _POPUP_FADE_TICK_MS / theme.animation.duration)


def _cancel_popup_fade(window: Gtk.Window) -> None:
    source_id = getattr(window, "_shell_fade_source_id", 0)
    if source_id:
        GLib.source_remove(source_id)
        setattr(window, "_shell_fade_source_id", 0)


def _fade_in_tick(window: Gtk.Window) -> bool:
    next_opacity = min(1.0, window.get_opacity() + _popup_fade_step())
    window.set_opacity(next_opacity)
    if next_opacity >= 1.0:
        setattr(window, "_shell_fade_source_id", 0)
        return False
    return True


def _fade_out_tick(window: Gtk.Window) -> bool:
    next_opacity = max(0.0, window.get_opacity() - _popup_fade_step())
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
    theme = active_theme()
    if theme is not None and not theme.animation.enabled:
        window.set_opacity(1.0)
        window.show_all()
        window.present()
        return
    window.set_opacity(0.0)
    window.show_all()
    window.present()
    source_id = GLib.timeout_add(_POPUP_FADE_TICK_MS, _fade_in_tick, window)
    setattr(window, "_shell_fade_source_id", source_id)


def hide_popup(window: Gtk.Window) -> None:
    """Hide a popup with disintegration (preferred) or a short opacity fade."""
    _cancel_popup_fade(window)
    if not window.get_visible():
        window.hide()
        window.set_opacity(1.0)
        return

    from .ui.disintegrate_hide import disintegrate_hide

    if disintegrate_hide(window):
        return

    theme = active_theme()
    if theme is not None and not theme.animation.enabled:
        window.hide()
        window.set_opacity(1.0)
        return
    source_id = GLib.timeout_add(_POPUP_FADE_TICK_MS, _fade_out_tick, window)
    setattr(window, "_shell_fade_source_id", source_id)


def _gdk_window_is_within(inner: Gdk.Window | None, outer: Gdk.Window | None) -> bool:
    if inner is None or outer is None:
        return False
    current: Gdk.Window | None = inner
    while current is not None:
        if current == outer:
            return True
        current = current.get_parent()
    return False


def _pointer_window_at(pointer) -> Gdk.Window | None:
    try:
        result = pointer.get_window_at_position()
    except Exception:
        return None
    if result is None:
        return None
    if isinstance(result, tuple):
        return result[0]
    return result


def _unpack_device_position(result) -> tuple[object | None, int, int]:
    if result is None:
        return None, 0, 0
    if not isinstance(result, tuple):
        return result, 0, 0
    if len(result) == 4:
        window, x, y, _mask = result
        return window, int(x), int(y)
    if len(result) == 3:
        first, second, third = result
        if first is None or hasattr(first, "get_origin"):
            return first, int(second), int(third)
        return None, int(first), int(second)
    return None, 0, 0


def _coords_in_widget_allocation(widget: Gtk.Widget, x: int, y: int) -> bool:
    allocation = widget.get_allocation()
    if widget.get_has_window():
        left, top = 0, 0
    else:
        left, top = int(allocation.x), int(allocation.y)
    width = int(allocation.width)
    height = int(allocation.height)
    return left <= x < left + width and top <= y < top + height


def pointer_inside_widget(widget: Gtk.Widget) -> bool:
    """True if the pointer is over ``widget``, using the Gdk window under it.

    Screen ``get_origin()`` + ``pointer.get_position()`` is wrong on Wayland
    after Hyprland moves a toplevel: GTK origin stays at (0, 0) while the
    compositor pointer is in global coordinates. That both keeps popups open
    after the pointer has left and closes them while the pointer is still on
    them. Hit-test the GdkWindow under the device instead.
    """
    if not widget.get_mapped() or not widget.get_visible():
        return False

    widget_window = widget.get_window()
    if widget_window is None:
        return False

    display = widget.get_display()
    seat = display.get_default_seat() if display is not None else None
    pointer = seat.get_pointer() if seat is not None else None
    if pointer is None:
        return False

    window_at = _pointer_window_at(pointer)
    if window_at is not None and not _gdk_window_is_within(window_at, widget_window):
        return False

    if (
        window_at is not None
        and widget.get_has_window()
        and _gdk_window_is_within(window_at, widget_window)
    ):
        return True

    try:
        position = widget_window.get_device_position(pointer)
    except Exception:
        return window_at is not None

    child, x, y = _unpack_device_position(position)
    if window_at is None and child is None:
        return False
    return _coords_in_widget_allocation(widget, x, y)


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


def _is_grab_crossing(event: object | None) -> bool:
    if event is None:
        return False
    mode = getattr(event, "mode", None)
    return mode in (Gdk.CrossingMode.GRAB, Gdk.CrossingMode.UNGRAB)


def is_pointer_leaving_surface(event: object | None) -> bool:
    """True for a real leave of this widget's surface, not child or grab crossings."""
    if _is_grab_crossing(event):
        return False
    if event is None:
        return True
    detail = getattr(event, "detail", None)
    if detail == Gdk.NotifyType.INFERIOR:
        return False
    return True


def _event_button_number(event) -> int:
    try:
        ok, button = event.get_button()
        if ok and int(button) > 0:
            return int(button)
    except Exception:
        pass
    button_field = getattr(event, "button", None)
    if isinstance(button_field, int):
        return button_field
    if button_field is not None:
        try:
            return int(button_field.button)
        except Exception:
            pass
    return 0


def _event_gdk_window(event) -> Gdk.Window | None:
    try:
        window = event.get_window()
        if window is not None:
            return window
    except Exception:
        pass
    return getattr(event, "window", None)


def _collect_active_probes() -> list[PopupOutsideDismiss]:
    probes: list[PopupOutsideDismiss] = []
    seen: set[int] = set()
    for shell in list(Gtk.Window.list_toplevels()):
        registered = getattr(shell, _SHELL_PROBES_ATTR, None) or ()
        for probe in list(registered):
            marker = id(probe)
            if marker in seen:
                continue
            seen.add(marker)
            probes.append(probe)
    return probes


def _gdk_global_event_handler(event) -> None:
    """See every GDK event for this process, then continue normal GTK dispatch.

    Used so bar-button presses (which never bubble to ``Gtk.Window``) still
    dismiss open popups — without eating the click.
    """
    try:
        if event.type == Gdk.EventType.BUTTON_PRESS:
            button = _event_button_number(event)
            if button in (1, 3):
                event_window = _event_gdk_window(event)
                for probe in _collect_active_probes():
                    probe.on_gdk_button_press(button, event_window)
    except Exception:
        pass
    Gtk.main_do_event(event)


def _ensure_global_event_handler() -> None:
    global _event_handler_installed
    if _event_handler_installed:
        return
    Gdk.event_handler_set(_gdk_global_event_handler)
    _event_handler_installed = True


class PopupOutsideDismiss:
    """Hide popups on outside click without eating that click.

    Close triggers:

    * Button press on the shell bar outside the popup/anchor (global GDK handler)
    * ``dismiss_open_popups_if_pointer_outside()`` — Hyprland mouse *press*
      (non-consuming ``gapplication action``) after the click reaches its target

    Never closes on GTK ``focus-out``, Hyprland focus-follows-mouse, or pointer leave.
    Notification toasts keep their own expire timers.
    """

    _INSTALL_GRACE_USEC = 200_000

    def __init__(self) -> None:
        self._popup: Gtk.Window | None = None
        self._shell_window: Gtk.Window | None = None
        self._anchors: tuple[Gtk.Widget, ...] = ()
        self._on_dismiss: Callable[[], None] | None = None
        self._popup_title: str = ""
        self._install_grace_until: int = 0
        self._dismiss_generation: int = 0
        self._extra_windows: tuple[Gtk.Window, ...] = ()
        self._suspended = False

    def install(
        self,
        popup: Gtk.Window,
        shell_window: Gtk.Window,
        anchor_widgets: tuple[Gtk.Widget, ...],
        on_dismiss: Callable[[], None],
        event_bus=None,
        extra_windows: tuple[Gtk.Window, ...] = (),
    ) -> None:
        del event_bus  # Kept for call-site compatibility; focus/active-window unused.
        self.uninstall()
        self._dismiss_generation += 1
        self._popup = popup
        self._shell_window = shell_window
        self._anchors = anchor_widgets
        self._on_dismiss = on_dismiss
        self._popup_title = (popup.get_title() or "").strip()
        self._install_grace_until = GLib.get_monotonic_time() + self._INSTALL_GRACE_USEC
        self._extra_windows = extra_windows
        self._register_shell_probe(shell_window)
        _ensure_global_event_handler()

    def uninstall(self) -> None:
        self._dismiss_generation += 1
        if self._shell_window is not None:
            self._unregister_shell_probe(self._shell_window)
        self._popup = None
        self._shell_window = None
        self._anchors = ()
        self._on_dismiss = None
        self._popup_title = ""
        self._extra_windows = ()
        self._install_grace_until = 0
        self._suspended = False

    def suspend(self) -> None:
        """Keep the popup open while a drag crosses its surface."""
        self._suspended = True

    def resume(self) -> None:
        self._suspended = False

    def set_extra_windows(self, extra_windows: tuple[Gtk.Window, ...]) -> None:
        self._extra_windows = extra_windows

    def _register_shell_probe(self, shell_window: Gtk.Window) -> None:
        probes = getattr(shell_window, _SHELL_PROBES_ATTR, None)
        if probes is None:
            probes = []
            setattr(shell_window, _SHELL_PROBES_ATTR, probes)
        if self not in probes:
            probes.append(self)

    def _unregister_shell_probe(self, shell_window: Gtk.Window) -> None:
        probes = getattr(shell_window, _SHELL_PROBES_ATTR, None)
        if not probes:
            return
        try:
            probes.remove(self)
        except ValueError:
            pass

    def _owned_gdk_windows(self) -> list[Gdk.Window]:
        windows: list[Gdk.Window] = []
        for candidate in (self._popup, *self._extra_windows):
            if candidate is None or not candidate.get_visible():
                continue
            gdk_window = candidate.get_window()
            if gdk_window is not None:
                windows.append(gdk_window)
        return windows

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

    def _press_on_owned_popup(self, event_window: Gdk.Window | None) -> bool:
        if event_window is None:
            return False
        for owned in self._owned_gdk_windows():
            if _gdk_window_is_within(event_window, owned):
                return True
        return False

    def _press_on_shell_bar(self, event_window: Gdk.Window | None) -> bool:
        shell = self._shell_window
        if shell is None or event_window is None:
            return False
        shell_gdk = shell.get_window()
        if shell_gdk is None:
            return False
        return _gdk_window_is_within(event_window, shell_gdk)

    def on_gdk_button_press(self, button: int, event_window: Gdk.Window | None) -> None:
        """Bar press outside the popup → dismiss."""
        if self._suspended:
            return
        if GLib.get_monotonic_time() < self._install_grace_until:
            return
        if button not in (1, 3):
            return
        popup = self._popup
        if popup is None or not popup.get_visible():
            return
        if self._press_on_owned_popup(event_window):
            return
        if not self._press_on_shell_bar(event_window):
            return
        for anchor in self._anchors:
            if pointer_inside_widget(anchor):
                return
        self._dismiss()

    def on_shell_bar_button_press(self, button: int) -> None:
        """Direct bar-press notify (tests and callers without a Gdk event window)."""
        if self._suspended:
            return
        if GLib.get_monotonic_time() < self._install_grace_until:
            return
        if button not in (1, 3):
            return
        popup = self._popup
        if popup is None or not popup.get_visible():
            return
        if pointer_inside_widget(popup):
            return
        for extra in self._extra_windows:
            if extra.get_visible() and pointer_inside_window(extra):
                return
        for anchor in self._anchors:
            if pointer_inside_widget(anchor):
                return
        self._dismiss()

    def dismiss_if_pointer_outside(self) -> None:
        """Hyprland mouse-press hook: close only when the pointer is outside."""
        if self._suspended:
            return
        if GLib.get_monotonic_time() < self._install_grace_until:
            return
        popup = self._popup
        if popup is None or not popup.get_visible():
            return
        if self._pointer_over_popup_or_anchor():
            return
        self._dismiss()

    def _dismiss(self) -> bool:
        if self._suspended:
            return False
        on_dismiss = self._on_dismiss
        self.uninstall()
        if on_dismiss is not None:
            on_dismiss()
        return False


def dismiss_open_popups_if_pointer_outside() -> None:
    """Close every tracked shell popup whose pointer is not over it/its anchor.

    Intended for Hyprland mouse-press binds (non-consuming): the click still
    reaches its target while we hide dangling Jugoo popups immediately.
    """
    for probe in _collect_active_probes():
        try:
            probe.dismiss_if_pointer_outside()
        except Exception:
            continue
