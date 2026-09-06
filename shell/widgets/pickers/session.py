"""GTK-free picker interaction: open, close, search, keyboard selection."""

from __future__ import annotations

from dataclasses import dataclass


KEY_ESCAPE = "Escape"
KEY_RETURN = "Return"
KEY_KP_ENTER = "KP_Enter"
KEY_UP = "Up"
KEY_DOWN = "Down"
KEY_LEFT = "Left"
KEY_RIGHT = "Right"
KEY_KP_UP = "KP_Up"
KEY_KP_DOWN = "KP_Down"
KEY_KP_LEFT = "KP_Left"
KEY_KP_RIGHT = "KP_Right"

ACTION_CLOSE = "close"
ACTION_SELECT = "select"
ACTION_MOVED = "moved"
ACTION_IGNORE = "ignore"

_VERTICAL = {
    KEY_UP: (0, -1),
    KEY_KP_UP: (0, -1),
    KEY_DOWN: (0, 1),
    KEY_KP_DOWN: (0, 1),
}
_HORIZONTAL = {
    KEY_LEFT: (-1, 0),
    KEY_KP_LEFT: (-1, 0),
    KEY_RIGHT: (1, 0),
    KEY_KP_RIGHT: (1, 0),
}


@dataclass
class PickerSession:
    """Navigation state shared by Search, Clipboard, and Emoji pickers."""

    columns: int = 1
    open: bool = False
    query: str = ""
    selected_index: int = -1
    item_count: int = 0

    def __post_init__(self) -> None:
        self.columns = max(1, int(self.columns))

    def open_session(self, item_count: int = 0) -> None:
        self.open = True
        self.query = ""
        self.set_items(item_count, reset_selection=True)

    def close_session(self) -> None:
        self.open = False
        self.query = ""
        self.selected_index = -1
        self.item_count = 0

    def set_items(self, item_count: int, *, reset_selection: bool = False) -> None:
        self.item_count = max(0, int(item_count))
        if self.item_count <= 0:
            self.selected_index = -1
            return
        if reset_selection or self.selected_index < 0:
            self.selected_index = 0
            return
        if self.selected_index >= self.item_count:
            self.selected_index = self.item_count - 1

    def select_index(self, index: int) -> int:
        if self.item_count <= 0:
            self.selected_index = -1
            return self.selected_index
        self.selected_index = max(0, min(self.item_count - 1, index))
        return self.selected_index

    def move(self, dx: int = 0, dy: int = 0) -> int:
        if self.item_count <= 0:
            self.selected_index = -1
            return self.selected_index
        index = 0 if self.selected_index < 0 else self.selected_index
        index += dy * self.columns
        index += dx
        return self.select_index(index)

    def handle_key(self, key: str) -> str:
        """Return close, select, moved, or ignore. Does nothing when closed."""
        if not self.open:
            return ACTION_IGNORE
        if key == KEY_ESCAPE:
            return ACTION_CLOSE
        if key in (KEY_RETURN, KEY_KP_ENTER):
            return ACTION_SELECT
        if key in _VERTICAL:
            self.move(*_VERTICAL[key])
            return ACTION_MOVED
        if key in _HORIZONTAL:
            if self.columns <= 1:
                return ACTION_IGNORE
            self.move(*_HORIZONTAL[key])
            return ACTION_MOVED
        return ACTION_IGNORE
