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
    assert CategoryId.BARRA in order
    assert CategoryId.NOTIFICACIONES in order


def test_ordered_categories_inserts_atajos_before_avanzado() -> None:
    class Manager:
        def categories_present(self):
            return (
                CategoryId.GENERAL,
                CategoryId.COMPORTAMIENTO,
                CategoryId.AVANZADO,
            )

    order = ordered_categories(Manager())  # type: ignore[arg-type]
    assert CategoryId.ATAJOS in order
    assert order.count(CategoryId.ATAJOS) == 1
    assert order.index(CategoryId.ATAJOS) < order.index(CategoryId.AVANZADO)
    assert order == (
        CategoryId.GENERAL,
        CategoryId.COMPORTAMIENTO,
        CategoryId.ATAJOS,
        CategoryId.AVANZADO,
    )


def test_ordered_categories_atajos_not_duplicated_if_already_present() -> None:
    class Manager:
        def categories_present(self):
            return (
                CategoryId.GENERAL,
                CategoryId.ATAJOS,
                CategoryId.COMPORTAMIENTO,
                CategoryId.AVANZADO,
            )

    order = ordered_categories(Manager())  # type: ignore[arg-type]
    assert order.count(CategoryId.ATAJOS) == 1
    # Already present: leave relative position (do not re-insert).
    assert order.index(CategoryId.ATAJOS) < order.index(CategoryId.COMPORTAMIENTO)


def test_ordered_categories_atajos_without_being_in_categories_present() -> None:
    class Manager:
        def categories_present(self):
            return (CategoryId.GENERAL, CategoryId.BARRA, CategoryId.AVANZADO)

    present = Manager().categories_present()
    assert CategoryId.ATAJOS not in present
    order = ordered_categories(Manager())  # type: ignore[arg-type]
    assert CategoryId.ATAJOS in order
    assert order.index(CategoryId.BARRA) < order.index(CategoryId.ATAJOS)
    assert order.index(CategoryId.ATAJOS) < order.index(CategoryId.AVANZADO)


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


if __name__ == "__main__":
    test_settings_controller_routes_to_general()
    test_ordered_categories_keeps_general_first()
    test_ordered_categories_inserts_atajos_before_avanzado()
    test_ordered_categories_atajos_not_duplicated_if_already_present()
    test_ordered_categories_atajos_without_being_in_categories_present()
    test_schema_contains_avatar_path_setting()
    test_schema_contains_machine_and_profile_settings()
    test_profile_fields_round_trip()
    test_search_destination_constant()
    print("ok")
