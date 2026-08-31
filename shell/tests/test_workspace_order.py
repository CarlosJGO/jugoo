from shell.models import Workspace, reorder_workspace_order


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
