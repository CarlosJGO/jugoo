"""Animated keyboard cat that reacts to global typing activity.

Not a ShellModule: sits free on the bar (no capsule), normally to the right of
pinned apps, bottom-aligned with the bar edge.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib, Gtk

from ...config import KEYBOARD_CAT_VERTICAL_INSET
from ...eventbus import EventBus
from ...identity import assets_dir
from ...servicios.teclado.actividad import (
    KEY_PRESS,
    KEY_RELEASE,
    KEYBOARD_ACTIVITY,
    KEYBOARD_LISTENER_STATUS,
)

_STANDY = "keyboard_standy.svg"
_FRAME1 = "keyboard_frame1.svg"
_FRAME2 = "keyboard_frame2.svg"

_PROVISIONAL_SIZE = 22
# Safety ceiling only (SVG blow-up guard). Real size tracks the bar height.
_MAX_PIXEL_SIZE = 96


def keyboard_cat_assets_dir() -> Path:
    return assets_dir() / "cat" / "keyboard"


def pixel_size_for_bar_height(
    bar_height: int,
    *,
    inset: int = KEYBOARD_CAT_VERTICAL_INSET,
    max_size: int = _MAX_PIXEL_SIZE,
) -> int:
    """Square size from bar height; never larger than the bar itself."""
    height = max(1, int(bar_height))
    size = max(1, height - max(0, inset) * 2)
    return max(1, min(size, max_size, height))


class KeyboardCatWidget(Gtk.Box):
    """Idle/typing SVG animation; transparent, bottom-aligned, no module chrome."""

    def __init__(self, event_bus: EventBus, bar_host: Gtk.Widget) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.get_style_context().add_class("keyboard-cat-widget")
        self.set_valign(Gtk.Align.END)
        self.set_halign(Gtk.Align.START)
        self.set_hexpand(False)
        self.set_vexpand(False)

        self._event_bus = event_bus
        self._bar_host = bar_host
        self._assets = keyboard_cat_assets_dir()
        self._pixel_size = _PROVISIONAL_SIZE
        self._frame_index = 0
        self._next_frame = 0
        self._pressed_codes: set[int] = set()
        self._bar_allocate_id = 0
        self._standy: GdkPixbuf.Pixbuf | None = None
        self._frames: tuple[GdkPixbuf.Pixbuf, ...] = ()

        self._image = Gtk.Image()
        self._image.get_style_context().add_class("keyboard-cat-image")
        self._image.set_valign(Gtk.Align.END)
        self._image.set_halign(Gtk.Align.CENTER)
        self._image.set_size_request(_PROVISIONAL_SIZE, _PROVISIONAL_SIZE)
        self.pack_start(self._image, False, False, 0)

        self.set_tooltip_text("Gato teclado")
        self._reload_pixbufs(self._pixel_size)
        self._show_standy()

        self._bar_allocate_id = bar_host.connect(
            "size-allocate",
            self._on_bar_size_allocate,
        )
        self._event_bus.subscribe(KEYBOARD_ACTIVITY, self._on_keyboard_activity)
        self._event_bus.subscribe(KEYBOARD_LISTENER_STATUS, self._on_listener_status)
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._fit_to_bar)

    def _on_destroy(self, *_args) -> None:
        self._event_bus.unsubscribe(KEYBOARD_ACTIVITY, self._on_keyboard_activity)
        self._event_bus.unsubscribe(KEYBOARD_LISTENER_STATUS, self._on_listener_status)
        if self._bar_allocate_id:
            self._bar_host.disconnect(self._bar_allocate_id)
            self._bar_allocate_id = 0

    def _on_listener_status(self, status: object) -> None:
        if status == "listening":
            self.set_tooltip_text("Gato teclado")
        elif status == "no_permission":
            self.set_tooltip_text(
                "Gato teclado — sin acceso a /dev/input\n"
                "sudo usermod -aG input $USER  (luego re-login)"
            )
        elif status == "no_device":
            self.set_tooltip_text("Gato teclado — no se encontró teclado")

    def _on_bar_size_allocate(self, _widget: Gtk.Widget, allocation) -> None:
        self._apply_bar_height(int(allocation.height))

    def _fit_to_bar(self) -> bool:
        height = int(self._bar_host.get_allocated_height())
        if height > 1:
            self._apply_bar_height(height)
        return False

    def _apply_bar_height(self, bar_height: int) -> None:
        if bar_height <= 1:
            return
        # Prefer sibling natural height so we track the bar without chasing our
        # own allocation (allocated peer heights inflate with us).
        peer_natural = self._sibling_natural_height()
        target = bar_height if peer_natural <= 0 else min(bar_height, peer_natural)
        size = pixel_size_for_bar_height(target)
        size = min(size, bar_height)
        if size == self._pixel_size and self._standy is not None:
            return
        self._pixel_size = size
        self._reload_pixbufs(size)
        if self._pressed_codes:
            self._show_frame(self._frame_index)
        else:
            self._show_standy()

    def _sibling_natural_height(self) -> int:
        parent = self.get_parent()
        if parent is None:
            return 0
        tallest = 0
        for child in parent.get_children():
            if child is self:
                continue
            _minimum, natural = child.get_preferred_height()
            tallest = max(tallest, int(natural))
        return tallest

    def _reload_pixbufs(self, pixel_size: int) -> None:
        size = max(1, min(int(pixel_size), _MAX_PIXEL_SIZE))
        standy = self._load_pixbuf(_STANDY, size)
        frame1 = self._load_pixbuf(_FRAME1, size)
        frame2 = self._load_pixbuf(_FRAME2, size)
        self._standy = standy
        frames = tuple(p for p in (frame1, frame2) if p is not None)
        self._frames = frames if frames else ((standy,) if standy is not None else ())
        # Square requisition keeps SVG aspect ratio (width follows height).
        self._image.set_size_request(size, size)

    def _load_pixbuf(self, name: str, pixel_size: int) -> GdkPixbuf.Pixbuf | None:
        path = self._assets / name
        if not path.is_file():
            return None
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(
                str(path),
                pixel_size,
                pixel_size,
            )
        except GLib.Error:
            return None

    def _on_keyboard_activity(self, payload: object) -> None:
        kind = ""
        code = -1
        if isinstance(payload, dict):
            kind = str(payload.get("kind", ""))
            try:
                code = int(payload.get("code", -1))
            except (TypeError, ValueError):
                code = -1
        if kind == KEY_PRESS:
            self._on_key_press(code)
        elif kind == KEY_RELEASE:
            self._on_key_release(code)

    def _on_key_press(self, code: int) -> None:
        if code < 0 or code in self._pressed_codes:
            return
        self._pressed_codes.add(code)
        if not self._frames:
            return
        # Every new physical press alternates paws (fast typing included).
        self._frame_index = self._next_frame % len(self._frames)
        self._next_frame = (self._next_frame + 1) % len(self._frames)
        self._show_frame(self._frame_index)

    def _on_key_release(self, code: int) -> None:
        if code < 0:
            return
        self._pressed_codes.discard(code)
        if not self._pressed_codes:
            self._show_standy()

    def _show_standy(self) -> None:
        if self._standy is not None:
            self._image.set_from_pixbuf(self._standy)

    def _show_frame(self, index: int) -> None:
        if not self._frames:
            self._show_standy()
            return
        pixbuf = self._frames[index % len(self._frames)]
        self._image.set_from_pixbuf(pixbuf)
