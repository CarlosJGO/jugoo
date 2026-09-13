"""Persistent island host: same widget identity across attach/detach."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from .animator import IslandAnimator, lerp
from .placeholder import IslandPlaceholder
from .stage import IslandStage
from .states import IslandState

_DETACH_MS = 120
_EXPAND_MS = 160
_COLLAPSE_MS = 140
_RETURN_MS = 120
_DROP_PX = 12


@dataclass(frozen=True)
class _Geom:
    x: int
    y: int
    w: int
    h: int


def _window_origin(window: Gdk.Window) -> tuple[int, int]:
    origin = window.get_origin()
    if isinstance(origin, tuple) and len(origin) == 3:
        _ok, ox, oy = origin
        return int(ox), int(oy)
    if isinstance(origin, tuple) and len(origin) == 2:
        return int(origin[0]), int(origin[1])
    return 0, 0


class OrganicIslandHost(Gtk.Box):
    """Wraps compact bar chrome + expanded content as one continuous entity."""

    def __init__(
        self,
        island_id: str,
        *,
        compact: Gtk.Widget,
        expanded: Gtk.Widget,
        stage: IslandStage,
        on_surface_expand: Callable[[int, int], None],
        on_surface_restore: Callable[[], None],
        expanded_width: int,
        expanded_height: int,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("organic-island")
        self._island_id = island_id
        self._compact = compact
        self._expanded = expanded
        self._stage = stage
        self._on_surface_expand = on_surface_expand
        self._on_surface_restore = on_surface_restore
        self._expanded_width = int(expanded_width)
        self._expanded_height = int(expanded_height)
        self._on_closed = on_closed
        self._state = IslandState.ATTACHED
        self._animator = IslandAnimator()
        self._placeholder: IslandPlaceholder | None = None
        self._home_parent: Gtk.Container | None = None
        self._home_index = 0
        self._origin = _Geom(0, 0, 1, 1)
        self._bar_height = 1
        self._float_geom = _Geom(0, 0, 1, 1)
        self._outside_press_id = 0
        self._key_press_id = 0
        self._dismiss_target: Gtk.Widget | None = None

        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._card.get_style_context().add_class("organic-island-card")
        self._card.pack_start(compact, False, False, 0)
        expanded.set_no_show_all(True)
        expanded.hide()
        self._card.pack_start(expanded, True, True, 0)
        self.pack_start(self._card, False, False, 0)
        self.connect("destroy", self._on_destroy)

    @property
    def island_id(self) -> str:
        return self._island_id

    @property
    def state(self) -> IslandState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state.is_detached

    def toggle(self) -> None:
        if self._state == IslandState.ACTIVE:
            self.close()
        elif self._state == IslandState.ATTACHED:
            self.open()

    def open(self) -> None:
        if self._state != IslandState.ATTACHED or self._animator.running:
            return
        self._begin_detach()

    def close(self) -> None:
        if self._state != IslandState.ACTIVE or self._animator.running:
            return
        self._begin_collapse()

    def shutdown(self) -> None:
        """Cancel animations and dismiss handlers before the shell tears down."""
        self._animator.cancel()
        self._uninstall_dismiss()

    def _on_destroy(self, *_args) -> None:
        self._animator.cancel()
        self._uninstall_dismiss()
        if self._placeholder is not None:
            parent = self._placeholder.get_parent()
            if parent is not None:
                parent.remove(self._placeholder)
            self._placeholder = None
        self._stage.release(self)

    def _begin_detach(self) -> None:
        parent = self.get_parent()
        if not isinstance(parent, Gtk.Container):
            return
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Widget):
            return

        self._home_parent = parent
        children = list(parent.get_children())
        try:
            self._home_index = children.index(self)
        except ValueError:
            self._home_index = len(children)

        translated = self.translate_coordinates(toplevel, 0, 0)
        ox, oy = translated if translated else (0, 0)
        alloc = self.get_allocation()
        self._origin = _Geom(int(ox), int(oy), max(1, alloc.width), max(1, alloc.height))
        self._float_geom = self._origin
        bar_alloc = toplevel.get_allocation()
        self._bar_height = max(1, bar_alloc.height)

        self._placeholder = IslandPlaceholder(self._origin.w, self._origin.h)
        parent.remove(self)
        parent.pack_start(self._placeholder, False, False, 0)
        parent.reorder_child(self._placeholder, self._home_index)
        self._placeholder.show_all()

        surface_h = self._bar_height + self._expanded_height + _DROP_PX + 12
        self._on_surface_expand(self._bar_height, surface_h)

        self._state = IslandState.DETACHING
        self.get_style_context().add_class("detached")
        self.set_size_request(self._origin.w, self._origin.h)
        self._stage.place(self, self._origin.x, self._origin.y)
        self._compact.show_all()
        self._expanded.hide()

        start = self._origin
        end_w = self._expanded_width
        end_h = self._expanded_height
        end_x = self._clamp_x(start.x + (start.w - end_w) // 2, end_w)
        end_y = start.y + _DROP_PX

        def on_detach_progress(t: float) -> None:
            x = int(lerp(start.x, end_x, t * 0.4))
            y = int(lerp(start.y, end_y, t))
            w = int(lerp(start.w, end_w, t * 0.3))
            h = int(lerp(start.h, start.h + 6, t))
            self._apply_geom(x, y, w, h)

        def after_detach() -> None:
            self._state = IslandState.EXPANDING
            self._compact.hide()
            self._expanded.set_no_show_all(False)
            self._expanded.show_all()
            self.refresh_expanded()

            mid = self._float_geom

            def on_expand_progress(t: float) -> None:
                x = int(lerp(mid.x, end_x, t))
                y = int(lerp(mid.y, end_y, t))
                w = int(lerp(mid.w, end_w, t))
                h = int(lerp(mid.h, end_h, t))
                self._apply_geom(x, y, w, h)

            def after_expand() -> None:
                self._apply_geom(end_x, end_y, end_w, end_h)
                self._state = IslandState.ACTIVE
                self.get_style_context().add_class("active")
                self._install_dismiss()

            self._animator.animate(_EXPAND_MS, on_expand_progress, after_expand)

        self._animator.animate(_DETACH_MS, on_detach_progress, after_detach)

    def _begin_collapse(self) -> None:
        self._uninstall_dismiss()
        self._state = IslandState.COLLAPSING
        self.get_style_context().remove_class("active")

        cur = self._float_geom
        mid_h = max(self._origin.h + 6, cur.h // 2)
        mid_y = self._origin.y + _DROP_PX

        def on_collapse_progress(t: float) -> None:
            w = int(lerp(cur.w, self._origin.w, t))
            h = int(lerp(cur.h, mid_h, t))
            x = int(lerp(cur.x, self._origin.x, t * 0.55))
            y = int(lerp(cur.y, mid_y, t))
            self._apply_geom(x, y, w, h)

        def after_collapse() -> None:
            self._expanded.hide()
            self._expanded.set_no_show_all(True)
            self._compact.show_all()
            self._state = IslandState.RETURNING
            mid = self._float_geom

            def on_return_progress(t: float) -> None:
                x = int(lerp(mid.x, self._origin.x, t))
                y = int(lerp(mid.y, self._origin.y, t))
                w = int(lerp(mid.w, self._origin.w, t))
                h = int(lerp(mid.h, self._origin.h, t))
                self._apply_geom(x, y, w, h)

            def after_return() -> None:
                self._finish_reattach()

            self._animator.animate(_RETURN_MS, on_return_progress, after_return)

        self._animator.animate(_COLLAPSE_MS, on_collapse_progress, after_collapse)

    def _finish_reattach(self) -> None:
        self._animator.cancel()
        self._stage.release(self)

        if self._placeholder is not None:
            home = self._placeholder.get_parent()
            index = self._home_index
            if isinstance(home, Gtk.Container):
                try:
                    index = list(home.get_children()).index(self._placeholder)
                except ValueError:
                    index = self._home_index
                home.remove(self._placeholder)
                home.pack_start(self, False, False, 0)
                home.reorder_child(self, index)
            self._placeholder = None
        elif isinstance(self._home_parent, Gtk.Container):
            self._home_parent.pack_start(self, False, False, 0)
            self._home_parent.reorder_child(self, self._home_index)

        self.set_size_request(-1, -1)
        self.get_style_context().remove_class("detached")
        self.get_style_context().remove_class("active")
        self._expanded.hide()
        self._expanded.set_no_show_all(True)
        self._compact.show_all()
        self.show_all()
        self._expanded.hide()
        self._on_surface_restore()
        self._state = IslandState.ATTACHED
        self._home_parent = None
        if self._on_closed is not None:
            self._on_closed()

    def _clamp_x(self, x: int, width: int) -> int:
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Widget):
            avail = max(width + 8, toplevel.get_allocated_width())
            return max(4, min(int(x), avail - width - 4))
        return int(x)

    def _apply_geom(self, x: int, y: int, w: int, h: int) -> None:
        geom = _Geom(int(x), int(y), max(1, int(w)), max(1, int(h)))
        self._float_geom = geom
        self.set_size_request(geom.w, geom.h)
        self._stage.move(self, geom.x, geom.y)
        self.queue_resize()

    def _install_dismiss(self) -> None:
        self._uninstall_dismiss()
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            return
        self._dismiss_target = toplevel
        self._outside_press_id = toplevel.connect(
            "button-press-event",
            self._on_outside_press,
        )
        self._key_press_id = toplevel.connect("key-press-event", self._on_key_press)

    def _uninstall_dismiss(self) -> None:
        target = self._dismiss_target
        if target is not None:
            if self._outside_press_id:
                try:
                    target.disconnect(self._outside_press_id)
                except TypeError:
                    pass
            if self._key_press_id:
                try:
                    target.disconnect(self._key_press_id)
                except TypeError:
                    pass
        self._outside_press_id = 0
        self._key_press_id = 0
        self._dismiss_target = None

    def _on_outside_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if self._state != IslandState.ACTIVE:
            return False
        if self._pointer_inside(event.x_root, event.y_root):
            return False
        # Only treat presses on the expanded surface below the bar as dismiss,
        # so other bar modules keep working; Escape / toggle still close.
        if event.y_root is not None:
            window = self.get_toplevel().get_window() if self.get_toplevel() else None
            if window is not None:
                _wx, wy = _window_origin(window)
                if event.y_root < wy + self._bar_height:
                    return False
        GLib.idle_add(self.close)
        return False

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if self._state != IslandState.ACTIVE:
            return False
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _pointer_inside(self, root_x: float, root_y: float) -> bool:
        gdk_window = self.get_window()
        if gdk_window is None:
            return False
        wx, wy = _window_origin(gdk_window)
        alloc = self.get_allocation()
        return (
            wx <= root_x <= wx + alloc.width
            and wy <= root_y <= wy + alloc.height
        )
