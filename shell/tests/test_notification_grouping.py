"""Unit tests for notification history grouping (incl. interleaved arrivals)."""

from __future__ import annotations

from shell.models import NotificationSnapshot
from shell.widgets.notificaciones.notification_grouping import (
    GROUPING_MODE_APP,
    GROUPING_MODE_SUMMARY,
    group_notification_snapshots,
    grouping_key,
    parse_grouping_exceptions,
    resolve_grouping_mode,
)


def _snap(
    nid: int,
    *,
    app: str,
    summary: str,
    timestamp: float,
    desktop: str = "",
) -> NotificationSnapshot:
    return NotificationSnapshot(
        id=nid,
        app_name=app,
        app_icon="",
        summary=summary,
        body="",
        actions=(),
        urgency=1,
        timestamp=timestamp,
        expire_timeout_ms=5000,
        desktop_entry=desktop,
    )


def test_parse_grouping_exceptions() -> None:
    assert parse_grouping_exceptions("strawberry, Spotify") == frozenset(
        {"strawberry", "spotify"}
    )
    assert parse_grouping_exceptions("strawberry; whatsapp\nfoo") == frozenset(
        {"strawberry", "whatsapp", "foo"}
    )
    assert parse_grouping_exceptions("") == frozenset()


def test_resolve_mode_exceptions_flip_default() -> None:
    assert (
        resolve_grouping_mode(
            "strawberry",
            default_mode=GROUPING_MODE_SUMMARY,
            exceptions=frozenset({"strawberry"}),
        )
        == GROUPING_MODE_APP
    )
    assert (
        resolve_grouping_mode(
            "whatsapp",
            default_mode=GROUPING_MODE_SUMMARY,
            exceptions=frozenset({"strawberry"}),
        )
        == GROUPING_MODE_SUMMARY
    )
    assert (
        resolve_grouping_mode(
            "whatsapp",
            default_mode=GROUPING_MODE_APP,
            exceptions=frozenset({"whatsapp"}),
        )
        == GROUPING_MODE_SUMMARY
    )


def test_summary_mode_keeps_whatsapp_contacts_apart() -> None:
    a1 = _snap(1, app="WhatsApp", summary="Ana", timestamp=1.0)
    a2 = _snap(2, app="WhatsApp", summary="Ana", timestamp=3.0)
    b1 = _snap(3, app="WhatsApp", summary="Luis", timestamp=2.0)
    groups = group_notification_snapshots(
        (a1, b1, a2),
        default_mode=GROUPING_MODE_SUMMARY,
        exceptions=frozenset(),
    )
    assert len(groups) == 2
    by_summary = {group[0].summary: [item.id for item in group] for group in groups}
    assert by_summary["Ana"] == [2, 1]
    assert by_summary["Luis"] == [3]


def test_app_mode_merges_all_strawberry_tracks() -> None:
    t1 = _snap(1, app="Strawberry", summary="Song A", timestamp=1.0)
    t2 = _snap(2, app="Strawberry", summary="Song B", timestamp=3.0)
    t3 = _snap(3, app="Strawberry", summary="Song C", timestamp=2.0)
    groups = group_notification_snapshots(
        (t1, t2, t3),
        default_mode=GROUPING_MODE_SUMMARY,
        exceptions=frozenset({"strawberry"}),
    )
    assert len(groups) == 1
    assert [item.id for item in groups[0]] == [2, 3, 1]


def test_interleaved_arrivals_still_merge() -> None:
    """Same key merges even when another app sits between the arrivals."""
    wa1 = _snap(1, app="WhatsApp", summary="Ana", timestamp=1.0)
    sb = _snap(2, app="Strawberry", summary="Track", timestamp=2.0)
    wa2 = _snap(3, app="WhatsApp", summary="Ana", timestamp=3.0)
    groups = group_notification_snapshots(
        (wa1, sb, wa2),
        default_mode=GROUPING_MODE_SUMMARY,
        exceptions=frozenset({"strawberry"}),
    )
    assert len(groups) == 2
    whatsapp = next(group for group in groups if group[0].app_name == "WhatsApp")
    strawberry = next(group for group in groups if group[0].app_name == "Strawberry")
    assert [item.id for item in whatsapp] == [3, 1]
    assert [item.id for item in strawberry] == [2]


def test_desktop_entry_does_not_split_same_contact() -> None:
    first = _snap(
        1,
        app="WhatsApp",
        summary="Ana",
        timestamp=1.0,
        desktop="org.whatsapp.WhatsApp.desktop",
    )
    second = _snap(2, app="WhatsApp", summary="Ana", timestamp=2.0, desktop="")
    assert grouping_key(
        first,
        default_mode=GROUPING_MODE_SUMMARY,
        exceptions=frozenset(),
    ) == grouping_key(
        second,
        default_mode=GROUPING_MODE_SUMMARY,
        exceptions=frozenset(),
    )
    groups = group_notification_snapshots(
        (first, second),
        default_mode=GROUPING_MODE_SUMMARY,
        exceptions=frozenset(),
    )
    assert len(groups) == 1
    assert len(groups[0]) == 2


if __name__ == "__main__":
    test_parse_grouping_exceptions()
    test_resolve_mode_exceptions_flip_default()
    test_summary_mode_keeps_whatsapp_contacts_apart()
    test_app_mode_merges_all_strawberry_tracks()
    test_interleaved_arrivals_still_merge()
    test_desktop_entry_does_not_split_same_contact()
    print("ok")
