"""Disguise contracts: shape, visual render, content bounds, input region."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cairo
import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from .roles import DisguiseId


@dataclass(frozen=True)
class ContentInsets:
    """Pixel insets from the shaped host edges to the usable GTK content box."""

    top: int
    right: int
    bottom: int
    left: int


class Disguise(Protocol):
    id: DisguiseId

    def build_shape(self, cr: cairo.Context, width: float, height: float) -> None:
        """Append the closed silhouette path for ``width``×``height`` (no fill)."""

    def render(
        self,
        cr: cairo.Context,
        width: float,
        height: float,
        *,
        elapsed_ms: int = 0,
    ) -> None:
        """Paint inside the current clip (already restricted to the silhouette)."""

    def content_insets(self, width: float, height: float) -> ContentInsets:
        """Safe rectangular pocket for GTK widgets inside the silhouette."""

    def style_classes(self) -> tuple[str, ...]:
        ...


def add_disguise_classes(widget: Gtk.Widget, disguise_id: DisguiseId, *extra: str) -> None:
    style = widget.get_style_context()
    style.add_class("jugoo-disguise")
    style.add_class(f"disguise-{disguise_id.value}")
    for name in extra:
        style.add_class(name)
