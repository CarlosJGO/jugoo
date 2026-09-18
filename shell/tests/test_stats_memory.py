from __future__ import annotations

import sys
import types

cairo_module = types.ModuleType("cairo")
cairo_module.Operator = types.SimpleNamespace(CLEAR="CLEAR", OVER="OVER")
cairo_module.Context = object
sys.modules.setdefault("cairo", cairo_module)

gi_module = types.ModuleType("gi")
gi_module.require_version = lambda *args, **kwargs: None
sys.modules.setdefault("gi", gi_module)

repository_module = types.ModuleType("gi.repository")


class _DummyStyleContext:
    def add_class(self, *_args, **_kwargs) -> None:
        return None


class _DummyWidget:
    def __init__(self, *args, **kwargs) -> None:
        self._text = kwargs.get("label", "")

    def get_style_context(self) -> _DummyStyleContext:
        return _DummyStyleContext()

    def set_size_request(self, *_args, **_kwargs) -> None:
        return None

    def pack_start(self, *_args, **_kwargs) -> None:
        return None

    def add(self, *_args, **_kwargs) -> None:
        return None

    def connect(self, *_args, **_kwargs) -> None:
        return None

    def add_events(self, *_args, **_kwargs) -> None:
        return None

    def set_visible_window(self, *_args, **_kwargs) -> None:
        return None

    def set_hexpand(self, *_args, **_kwargs) -> None:
        return None

    def set_halign(self, *_args, **_kwargs) -> None:
        return None

    def set_valign(self, *_args, **_kwargs) -> None:
        return None

    def get_allocation(self):
        return types.SimpleNamespace(width=0, height=0)

    def set_name(self, *_args, **_kwargs) -> None:
        return None

    def set_text(self, value: str) -> None:
        self._text = value

    def get_text(self) -> str:
        return self._text

    @staticmethod
    def get_default():
        return None


class _DummyLabel(_DummyWidget):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text = kwargs.get("label", args[0] if args else "")

    def set_text(self, text: str) -> None:
        self.text = text


class _DummyBox(_DummyWidget):
    pass


class _DummyWindow(_DummyWidget):
    pass


class _DummyEventBox(_DummyWidget):
    pass


class _DummyOverlay(_DummyWidget):
    def add_overlay(self, *_args, **_kwargs) -> None:
        return None


class _DummyDrawingArea(_DummyWidget):
    pass


repository_module.Gdk = types.SimpleNamespace(
    EventMask=types.SimpleNamespace(BUTTON_PRESS_MASK=1),
    WindowTypeHint=types.SimpleNamespace(UTILITY=1, NOTIFICATION=2),
)
repository_module.GLib = types.SimpleNamespace(
    SOURCE_CONTINUE=True,
    timeout_add_seconds=lambda *args, **kwargs: None,
    idle_add=lambda *args, **kwargs: 0,
    source_remove=lambda *args, **kwargs: None,
)
repository_module.Gio = types.SimpleNamespace()
repository_module.Gtk = types.SimpleNamespace(
    Window=_DummyWindow,
    Widget=_DummyWidget,
    Box=_DummyBox,
    Label=_DummyLabel,
    EventBox=_DummyEventBox,
    Overlay=_DummyOverlay,
    DrawingArea=_DummyDrawingArea,
    Orientation=types.SimpleNamespace(VERTICAL="vertical", HORIZONTAL="horizontal"),
    Align=types.SimpleNamespace(CENTER="center"),
    WindowType=types.SimpleNamespace(TOPLEVEL=1),
    Application=types.SimpleNamespace(get_default=lambda: None),
)
repository_module.__getattr__ = lambda name: types.SimpleNamespace()
sys.modules.setdefault("gi.repository", repository_module)

from shell.servicios.sistema.system import MemoryStats, SystemStats
from shell.widgets.barra.stats import StatsWidget


class _TestLabel:
    def __init__(self) -> None:
        self.text = None

    def set_text(self, text: str) -> None:
        self.text = text


def test_stats_widget_uses_total_system_ram_usage_not_app_only() -> None:
    widget = object.__new__(StatsWidget)
    widget._memory_percent_label = _TestLabel()
    widget._memory_value_label = _TestLabel()
    widget._popup = type("Popup", (), {"maybe": None})()

    stats = SystemStats(
        memory=MemoryStats(
            total_bytes=10_000,
            available_bytes=4_000,
            applications_bytes=2_000,
        )
    )

    widget._update_memory(stats)

    assert widget._memory_percent_label.text == "RAM 60%"
    assert widget._memory_value_label.text == "6 KB"
