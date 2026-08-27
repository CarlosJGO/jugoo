"""Reserved module for media playback state."""

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk


class MediaWidget(Gtk.Box):
    # Will render media controls and metadata when that module is introduced.
    pass
