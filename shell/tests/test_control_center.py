"""Safe tests for unified control-center wiring helpers."""

from __future__ import annotations

from shell.controllers.settings import SettingsController
from shell.settings.schema import CategoryId, settings_by_key
from shell.widgets.configuraciones.pane import SEARCH_DESTINATION, ordered_categories


class _FakeManager:
    def categories_present(self):
        return (CategoryId.NOTIFICACIONES, CategoryId.GENERAL, CategoryId.BARRA)


def test_settings_controller_routes_to_general() -> None:
    called: list[CategoryId] = []
    closed = {"count": 0}

    def _open(category: CategoryId) -> None:
        called.append(category)

    def _close() -> None:
        closed["count"] += 1

    controller = SettingsController(open_settings=_open, close_center=_close)
    controller.toggle()
    assert called == [CategoryId.GENERAL]
    controller.close()
    assert closed["count"] == 1


def test_ordered_categories_keeps_general_first() -> None:
    order = ordered_categories(_FakeManager())  # type: ignore[arg-type]
    assert order[0] is CategoryId.GENERAL
    assert len(order) == 3
    assert CategoryId.BARRA in order


def test_schema_contains_avatar_path_setting() -> None:
    definitions = settings_by_key()
    setting = definitions["general.avatar_path"]
    assert setting.category is CategoryId.GENERAL
    assert setting.value_type == "path"
    assert "assets/usuario" in setting.description


def test_schema_contains_machine_and_profile_settings() -> None:
    definitions = settings_by_key()
    machine = definitions["general.machine_image_path"]
    fields = definitions["general.profile_fields_json"]
    assert machine.value_type == "path"
    assert "assets/pc" in machine.description
    assert fields.value_type == "string"
    assert fields.default == "[]"


def test_profile_fields_round_trip() -> None:
    from shell.settings.profile_model import ProfileField, parse_profile_fields, serialize_profile_fields

    raw = serialize_profile_fields(
        [
            ProfileField("Universidad", "Universidad X"),
            ProfileField("GitHub", "usuario123"),
        ]
    )
    parsed = parse_profile_fields(raw)
    assert len(parsed) == 2
    assert parsed[0].title == "Universidad"
    assert parsed[1].value == "usuario123"
    # Dict shape also accepted.
    from_dict = parse_profile_fields('{"Discord": "user"}')
    assert from_dict == (ProfileField("Discord", "user"),)


def test_search_destination_constant() -> None:
    assert SEARCH_DESTINATION == "__search__"


def test_search_row_reveals_entry_and_checks_after_settings_first() -> None:
    """Opening options first skips the search row; switching back must show it."""
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    from shell.widgets.pickers.overlay import PickerOverlay

    if not Gtk.init_check()[0]:
        raise AssertionError("GTK could not initialize")

    window = Gtk.Window()
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    entry = Gtk.SearchEntry()
    hidden = Gtk.CheckButton(label="Ocultas")
    ignored = Gtk.CheckButton(label="Ignoradas")
    row.pack_start(entry, True, True, 0)
    row.pack_end(hidden, False, False, 0)
    row.pack_end(ignored, False, False, 0)
    root.pack_start(row, False, False, 0)
    window.add(root)

    host = type("Host", (), {"_search_row": row})()
    PickerOverlay.set_search_row_visible(host, False)
    window.show_all()
    assert entry.get_visible() is False
    assert hidden.get_visible() is False
    assert ignored.get_visible() is False

    PickerOverlay.set_search_row_visible(host, True)
    assert entry.get_visible() is True
    assert hidden.get_visible() is True
    assert ignored.get_visible() is True
    window.destroy()


if __name__ == "__main__":
    test_settings_controller_routes_to_general()
    test_ordered_categories_keeps_general_first()
    test_schema_contains_avatar_path_setting()
    test_schema_contains_machine_and_profile_settings()
    test_profile_fields_round_trip()
    test_search_destination_constant()
    test_search_row_reveals_entry_and_checks_after_settings_first()
    print("ok")
