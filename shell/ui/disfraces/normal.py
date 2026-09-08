"""Default rectangular panel — no silhouette mask."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from .base import ContentInsets, add_disguise_classes
from .roles import DisguiseId


class NormalDisguise:
    id = DisguiseId.NORMAL

    def wrap(self, content: Gtk.Widget) -> Gtk.Widget:
        add_disguise_classes(content, self.id, *self.style_classes())
        return content

    def style_widget(self, widget: Gtk.Widget) -> None:
        add_disguise_classes(widget, self.id, *self.style_classes())

    def style_classes(self) -> tuple[str, ...]:
        return ()

    def build_shape(self, cr: cairo.Context, width: float, height: float) -> None:
        cr.rectangle(0, 0, width, height)

    def content_insets(self, width: float, height: float) -> ContentInsets:
        return ContentInsets(0, 0, 0, 0)

    def render(
        self,
        cr: cairo.Context,
        width: float,
        height: float,
        *,
        elapsed_ms: int = 0,
    ) -> None:
        return
