"""Tests for keyboard-cat sizing and input device discovery."""

from __future__ import annotations

from pathlib import Path

from shell.servicios.teclado.actividad import (
    discover_keyboard_device_paths,
    is_key_press_event,
    is_key_release_event,
    select_primary_keyboard,
)
from shell.widgets.barra.keyboard_cat import (
    keyboard_cat_assets_dir,
    pixel_size_for_bar_height,
)


def test_pixel_size_tracks_bar_square() -> None:
    assert pixel_size_for_bar_height(55, inset=0, max_size=96) == 55
    assert pixel_size_for_bar_height(55, inset=1, max_size=96) == 53
    assert pixel_size_for_bar_height(120, inset=0, max_size=96) == 96


def test_pixel_size_never_exceeds_bar() -> None:
    assert pixel_size_for_bar_height(40, inset=0, max_size=96) == 40
    assert pixel_size_for_bar_height(0, inset=0, max_size=96) == 1


def test_key_events_ignore_repeat() -> None:
    assert is_key_press_event(1, 1) is True
    assert is_key_press_event(1, 2) is False
    assert is_key_press_event(1, 0) is False
    assert is_key_release_event(1, 0) is True
    assert is_key_release_event(1, 1) is False
    assert is_key_release_event(0, 0) is False


def test_fast_typing_alternates_every_press() -> None:
    """Each new key-down switches frame; standy only when all keys are up."""
    pressed: set[int] = set()
    next_frame = 0
    shown: list[str] = []

    def on_press(code: int) -> None:
        nonlocal next_frame
        if code in pressed:
            return
        pressed.add(code)
        shown.append(f"frame{next_frame}")
        next_frame = (next_frame + 1) % 2

    def on_release(code: int) -> None:
        pressed.discard(code)
        if not pressed:
            shown.append("standy")

    on_press(30)
    on_release(30)
    on_press(30)
    on_release(30)
    # Overlapping fast keys still alternate on each press.
    on_press(31)
    on_press(32)
    on_release(31)
    on_release(32)
    assert shown == [
        "frame0",
        "standy",
        "frame1",
        "standy",
        "frame0",
        "frame1",
        "standy",
    ]


def test_select_primary_prefers_main_keyboard() -> None:
    paths = (
        Path("/dev/input/by-id/usb-MOUSE-if01-event-kbd"),
        Path("/dev/input/by-id/usb-SEMICO_USB_Gaming_Keyboard-event-kbd"),
        Path("/dev/input/by-id/usb-SEMICO_USB_Gaming_Keyboard-if01-event-kbd"),
    )
    assert select_primary_keyboard(paths) == (
        Path("/dev/input/by-id/usb-SEMICO_USB_Gaming_Keyboard-event-kbd"),
    )


def test_discover_prefers_by_id_symlinks(tmp_path: Path) -> None:
    by_id = tmp_path / "by-id"
    by_path = tmp_path / "by-path"
    by_id.mkdir(exist_ok=True)
    by_path.mkdir(exist_ok=True)
    event = tmp_path / "event4"
    event.write_bytes(b"")
    link = by_id / "usb-Test-Keyboard-event-kbd"
    link.symlink_to(event)

    found = discover_keyboard_device_paths(
        by_id=by_id,
        by_path=by_path,
        proc_devices=tmp_path / "missing-proc",
    )
    assert found == (link,)


def test_discover_falls_back_to_proc(tmp_path: Path) -> None:
    by_id = tmp_path / "by-id"
    by_path = tmp_path / "by-path"
    input_root = tmp_path / "input"
    by_id.mkdir(exist_ok=True)
    by_path.mkdir(exist_ok=True)
    input_root.mkdir(exist_ok=True)
    event = input_root / "event4"
    event.write_bytes(b"")
    proc = tmp_path / "devices"
    proc.write_text(
        "\n".join(
            [
                'N: Name="SEMICO USB Gaming Keyboard"',
                "H: Handlers=sysrq kbd leds event4",
                "B: EV=120013",
                "",
                'N: Name="Not A Pointer"',
                "H: Handlers=mouse0 event2",
                "B: EV=17",
                "",
            ]
        ),
        encoding="utf-8",
    )
    found = discover_keyboard_device_paths(
        by_id=by_id,
        by_path=by_path,
        proc_devices=proc,
        input_root=input_root,
    )
    assert found == (event,)


def test_keyboard_cat_assets_exist() -> None:
    root = keyboard_cat_assets_dir()
    assert (root / "keyboard_standy.svg").is_file()
    assert (root / "keyboard_frame1.svg").is_file()
    assert (root / "keyboard_frame2.svg").is_file()


if __name__ == "__main__":
    import tempfile

    test_pixel_size_tracks_bar_square()
    test_pixel_size_never_exceeds_bar()
    test_key_events_ignore_repeat()
    test_fast_typing_alternates_every_press()
    test_select_primary_prefers_main_keyboard()
    with tempfile.TemporaryDirectory() as raw:
        test_discover_prefers_by_id_symlinks(Path(raw))
    with tempfile.TemporaryDirectory() as raw:
        test_discover_falls_back_to_proc(Path(raw))
    test_keyboard_cat_assets_exist()
    print("ok")
