from __future__ import annotations

from shell.widgets.pickers.session import (
    ACTION_CLOSE,
    ACTION_IGNORE,
    ACTION_MOVED,
    ACTION_SELECT,
    PickerSession,
)


def test_open_and_close() -> None:
    session = PickerSession()
    session.open_session(4)
    assert session.open is True
    assert session.selected_index == 0
    assert session.item_count == 4
    session.close_session()
    assert session.open is False
    assert session.selected_index == -1
    assert session.handle_key("Return") == ACTION_IGNORE


def test_list_navigation_and_enter_escape() -> None:
    session = PickerSession(columns=1)
    session.open_session(3)
    assert session.handle_key("Down") == ACTION_MOVED
    assert session.selected_index == 1
    assert session.handle_key("Down") == ACTION_MOVED
    assert session.selected_index == 2
    session.handle_key("Down")
    assert session.selected_index == 2
    assert session.handle_key("Up") == ACTION_MOVED
    assert session.selected_index == 1
    assert session.handle_key("Left") == ACTION_IGNORE
    assert session.handle_key("Right") == ACTION_IGNORE
    assert session.handle_key("Return") == ACTION_SELECT
    assert session.handle_key("KP_Enter") == ACTION_SELECT
    assert session.handle_key("Escape") == ACTION_CLOSE


def test_grid_navigation_uses_arrows() -> None:
    session = PickerSession(columns=3)
    session.open_session(8)
    assert session.handle_key("Right") == ACTION_MOVED
    assert session.selected_index == 1
    assert session.handle_key("Down") == ACTION_MOVED
    assert session.selected_index == 4
    assert session.handle_key("Left") == ACTION_MOVED
    assert session.selected_index == 3
    session.select_index(7)
    session.handle_key("Right")
    assert session.selected_index == 7
    assert session.handle_key("Escape") == ACTION_CLOSE
    session.close_session()
    assert session.open is False


def _run() -> None:
    namespace = {name: value for name, value in globals().items() if name.startswith("test_")}
    for name, test in sorted(namespace.items()):
        test()
        print(f"ok {name}")


if __name__ == "__main__":
    _run()
