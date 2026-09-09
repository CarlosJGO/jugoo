"""History grouping keys for the notifications popup.

Groups merge by key across the whole history — arrival order (including
interleaved apps) does not matter.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ... import config as shell_config
from ...models import NotificationSnapshot
from ...servicios.notificaciones.notification_app import notification_app_key

GROUPING_MODE_SUMMARY = "summary"
GROUPING_MODE_APP = "app"
_VALID_MODES = frozenset({GROUPING_MODE_SUMMARY, GROUPING_MODE_APP})


def parse_grouping_exceptions(raw: str | None) -> frozenset[str]:
    """Parse a comma/semicolon/newline-separated list of app keys."""
    if not raw:
        return frozenset()
    parts: list[str] = []
    for chunk in str(raw).replace(";", ",").replace("\n", ",").split(","):
        key = chunk.strip().casefold()
        if key:
            parts.append(key)
    return frozenset(parts)


def normalize_grouping_mode(mode: str | None) -> str:
    text = str(mode or GROUPING_MODE_SUMMARY).strip().casefold()
    if text in _VALID_MODES:
        return text
    return GROUPING_MODE_SUMMARY


def resolve_grouping_mode(
    app_key: str,
    *,
    default_mode: str | None = None,
    exceptions: frozenset[str] | None = None,
) -> str:
    """Return the effective mode for one app (exceptions flip the default)."""
    default = normalize_grouping_mode(
        default_mode
        if default_mode is not None
        else getattr(shell_config, "NOTIFICATIONS_GROUPING_MODE", GROUPING_MODE_SUMMARY)
    )
    exception_keys = (
        exceptions
        if exceptions is not None
        else parse_grouping_exceptions(
            getattr(shell_config, "NOTIFICATIONS_GROUPING_EXCEPTIONS", "")
        )
    )
    key = str(app_key or "").strip().casefold()
    if key and key in exception_keys:
        return (
            GROUPING_MODE_APP
            if default == GROUPING_MODE_SUMMARY
            else GROUPING_MODE_SUMMARY
        )
    return default


def snapshot_app_key(snapshot: NotificationSnapshot) -> str:
    return notification_app_key(
        app_name=snapshot.app_name,
        app_icon=snapshot.app_icon or snapshot.desktop_entry,
    )


def grouping_key(
    snapshot: NotificationSnapshot,
    *,
    mode: str | None = None,
    default_mode: str | None = None,
    exceptions: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """Stable bucket key for one notification under the resolved grouping mode."""
    app_key = snapshot_app_key(snapshot)
    effective = mode or resolve_grouping_mode(
        app_key,
        default_mode=default_mode,
        exceptions=exceptions,
    )
    if effective == GROUPING_MODE_APP:
        return (GROUPING_MODE_APP, app_key or "application")

    summary = " ".join((snapshot.summary or "").split()).casefold() or "untitled"
    return (GROUPING_MODE_SUMMARY, app_key or "application", summary)


def group_notification_snapshots(
    snapshots: Sequence[NotificationSnapshot] | Iterable[NotificationSnapshot],
    *,
    default_mode: str | None = None,
    exceptions: frozenset[str] | None = None,
) -> list[list[NotificationSnapshot]]:
    """Bucket notifications by grouping key, newest-first within and across groups."""
    items = list(snapshots)
    buckets: dict[tuple[str, ...], list[NotificationSnapshot]] = {}
    for snapshot in items:
        key = grouping_key(
            snapshot,
            default_mode=default_mode,
            exceptions=exceptions,
        )
        buckets.setdefault(key, []).append(snapshot)

    grouped = list(buckets.values())
    for group in grouped:
        group.sort(key=lambda item: item.timestamp, reverse=True)
    grouped.sort(key=lambda group: group[0].timestamp, reverse=True)
    return grouped
