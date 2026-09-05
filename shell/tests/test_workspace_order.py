from shell.models import (
    Window,
    Workspace,
    compose_workspace_blocks,
    pick_temporary_workspace_id,
    plan_workspace_content_moves,
    reorder_workspace_order,
    swap_workspace_blocks,
)


def _workspace(workspace_id: int) -> Workspace:
    return Workspace(
        id=workspace_id,
        name=str(workspace_id),
        active=workspace_id == 1,
        windows=(),
        icons=(),
        focused_window_address=None,
        is_special=False,
    )


def test_reorder_workspace_order_moves_block_before_target() -> None:
    workspaces = (_workspace(1), _workspace(2), _workspace(3), _workspace(4))

    reordered = reorder_workspace_order(workspaces, 3, 2)

    assert [workspace.id for workspace in reordered] == [1, 3, 2, 4]


def test_swap_workspace_blocks_preserves_workspace_contents() -> None:
    first_window = Window("a", "kitty", "A", 1)
    second_window = Window("b", "firefox", "B", 4)
    first = Workspace(1, "1", False, (first_window,), ("terminal",), None)
    second = Workspace(4, "4", False, (second_window,), ("browser",), None)
    blocks = compose_workspace_blocks((first, _workspace(2), _workspace(3), second), 3)

    swapped = swap_workspace_blocks(blocks, 0, 1)

    assert [block.block_index for block in swapped] == [1, 0]
    assert swapped[0].workspaces[0] is second
    assert swapped[1].workspaces[0] is first
    assert swapped[0].workspaces[0].windows == (second_window,)
    assert swapped[1].workspaces[0].windows == (first_window,)


def test_pick_temporary_workspace_id_skips_occupied_ids() -> None:
    assert pick_temporary_workspace_id(()) == 9999
    assert pick_temporary_workspace_id((1, 9999, 9998)) == 9997


def test_plan_workspace_content_moves_direct_when_target_empty() -> None:
    moves = plan_workspace_content_moves(1, 4, ("0x1",), (), 9999)

    assert moves == (("0x1", 4),)


def test_plan_workspace_content_moves_swaps_through_temporary_workspace() -> None:
    moves = plan_workspace_content_moves(1, 4, ("0x1", "0x2"), ("0x3",), 9999)

    assert moves == (
        ("0x1", 9999),
        ("0x2", 9999),
        ("0x3", 1),
        ("0x1", 4),
        ("0x2", 4),
    )


def test_plan_workspace_content_moves_empty_source_does_not_steal_windows() -> None:
    moves = plan_workspace_content_moves(1, 4, (), ("0x3",), 9999)

    assert moves == ()


def test_plan_workspace_content_moves_skips_empty_or_invalid_pairs() -> None:
    assert plan_workspace_content_moves(1, 4, (), (), 9999) == ()
    assert plan_workspace_content_moves(1, 1, ("0x1",), ("0x2",), 9999) == ()
    assert plan_workspace_content_moves(-98, 4, ("0x1",), (), 9999) == ()
