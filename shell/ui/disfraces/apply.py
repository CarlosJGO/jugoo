"""Apply catalogued disguises to windows and content trees."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from .asteroid import AsteroidDisguise
from .catalog import disguise_for
from .mask import prepare_transparent_toplevel
from .meteor import MeteorDisguise
from .normal import NormalDisguise
from .roles import DisguiseId, WindowRole

_DISGUISES = {
    DisguiseId.NORMAL: NormalDisguise(),
    DisguiseId.ASTEROID: AsteroidDisguise(),
    DisguiseId.METEOR: MeteorDisguise(),
}


def resolve(disguise_id: DisguiseId):
    return _DISGUISES.get(disguise_id, _DISGUISES[DisguiseId.NORMAL])


def dress_content(role: WindowRole, content: Gtk.Widget) -> Gtk.Widget:
    """Wrap ``content`` with the disguise assigned to ``role``."""
    return resolve(disguise_for(role)).wrap(content)


def dress_widget(role: WindowRole, widget: Gtk.Widget) -> None:
    """Style an existing widget in-place (no structural wrap)."""
    resolve(disguise_for(role)).style_widget(widget)


def dress_window(window: Gtk.Window, role: WindowRole, content: Gtk.Widget) -> None:
    """Attach disguised content; shaped disguises get a true RGBA silhouette host."""
    disguise = resolve(disguise_for(role))
    disguise.style_widget(window)
    wrapped = disguise.wrap(content)
    if disguise.id is not DisguiseId.NORMAL:
        prepare_transparent_toplevel(window)
        window.add(wrapped)
        return
    # Rectangular surfaces share the bar's animated void + stars.
    from ..starfield import install_starfield, resolve_event_bus

    install_starfield(
        window,
        wrapped,
        resolve_event_bus(window),
        corner_radius=16.0,
    )
