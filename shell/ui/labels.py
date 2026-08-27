"""Typography helpers built on the shell text scale."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

TextRole = str

TEXT_ROLES: frozenset[TextRole] = frozenset({"title", "body", "caption", "muted"})


def shell_label(
    text: str,
    *,
    role: TextRole = "body",
    css_classes: tuple[str, ...] = (),
    xalign: float = 0.0,
    yalign: float = 0.5,
) -> Gtk.Label:
    """Create a label using the shared typography roles and optional module classes."""
    label = Gtk.Label(label=text, xalign=xalign, yalign=yalign)
    style = label.get_style_context()
    style.add_class("shell-text")
    if role in TEXT_ROLES:
        style.add_class(f"shell-text-{role}")
    for css_class in css_classes:
        style.add_class(css_class)
    return label
