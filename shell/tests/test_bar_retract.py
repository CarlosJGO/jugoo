"""Tests for top-bar retract geometry (no GTK required)."""

from __future__ import annotations

from shell.models import FloatingClient
from shell.servicios.escritorio.bar_retract import compute_bar_retract_px, step_retract_px
from shell.servicios.escritorio.hyprland import HyprlandService


def _float(
    *,
    x: int = 100,
    y: int = 20,
    width: int = 400,
    height: int = 300,
    app_class: str = "kitty",
    fullscreen: int = 0,
) -> FloatingClient:
    return FloatingClient(
        address="0x1",
        app_class=app_class,
        x=x,
        y=y,
        width=width,
        height=height,
        workspace_id=1,
        monitor=0,
        fullscreen=fullscreen,
    )


def test_no_overlap_means_no_retract() -> None:
    assert (
        compute_bar_retract_px(
            bar_top=0,
            bar_height=40,
            bar_left=0,
            bar_width=1920,
            gap_px=4,
            clients=(_float(y=80),),
        )
        == 0
    )


def test_partial_intrusion_retracts_only_needed() -> None:
    # Bar occupies y=0..40. Window top at y=20 with gap 4 → clear at 16 → retract 24.
    assert (
        compute_bar_retract_px(
            bar_top=0,
            bar_height=40,
            bar_left=0,
            bar_width=1920,
            gap_px=4,
            clients=(_float(y=20),),
        )
        == 24
    )


def test_full_push_retracts_entire_bar() -> None:
    assert (
        compute_bar_retract_px(
            bar_top=0,
            bar_height=40,
            bar_left=0,
            bar_width=1920,
            gap_px=4,
            clients=(_float(y=0),),
        )
        == 40
    )


def test_ignores_shell_and_fullscreen() -> None:
    assert (
        compute_bar_retract_px(
            bar_top=0,
            bar_height=40,
            bar_left=0,
            bar_width=1920,
            gap_px=4,
            clients=(
                _float(y=0, app_class="com.jugoo.Shell"),
                _float(y=0, fullscreen=1),
            ),
            ignore_classes=("com.jugoo.Shell",),
        )
        == 0
    )


def test_horizontal_miss_does_not_retract() -> None:
    assert (
        compute_bar_retract_px(
            bar_top=0,
            bar_height=40,
            bar_left=0,
            bar_width=800,
            gap_px=4,
            clients=(_float(x=900, y=0),),
        )
        == 0
    )


def test_step_retract_eases_toward_target() -> None:
    mid = step_retract_px(current=0.0, target=40.0)
    assert 0.0 < mid < 40.0
    assert step_retract_px(current=39.8, target=40.0) == 40.0


def test_floating_clients_from_raw_filters() -> None:
    raw = [
        {
            "address": "0xaaa",
            "class": "kitty",
            "floating": True,
            "mapped": True,
            "hidden": False,
            "visible": True,
            "at": [10, 20],
            "size": [100, 200],
            "workspace": {"id": 2},
            "monitor": 0,
            "fullscreen": 0,
            "pinned": False,
        },
        {
            "address": "0xbbb",
            "class": "tiled",
            "floating": False,
            "mapped": True,
            "hidden": False,
            "at": [0, 0],
            "size": [50, 50],
            "workspace": {"id": 1},
            "monitor": 0,
        },
        {
            "address": "0xccc",
            "class": "ghost",
            "floating": True,
            "mapped": False,
            "hidden": False,
            "at": [0, 0],
            "size": [50, 50],
            "workspace": {"id": 1},
            "monitor": 0,
        },
    ]
    clients = HyprlandService._floating_clients_from_raw(raw)
    assert len(clients) == 1
    assert clients[0].address == "0xaaa"
    assert clients[0].y == 20
    assert clients[0].workspace_id == 2


if __name__ == "__main__":
    test_no_overlap_means_no_retract()
    test_partial_intrusion_retracts_only_needed()
    test_full_push_retracts_entire_bar()
    test_ignores_shell_and_fullscreen()
    test_horizontal_miss_does_not_retract()
    test_step_retract_eases_toward_target()
    test_floating_clients_from_raw_filters()
    print("bar retract tests OK")
