"""Stacked-notifications window: cached block pages + coalesced slides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk

from ...config import (
    NOTIFICATION_GROUP_SLIDE_DURATION_MS,
    NOTIFICATION_POPUP_LIST_SPACING,
    NOTIFICATION_POPUP_MAX_HEIGHT,
    NOTIFICATION_POPUP_OFFSET,
    NOTIFICATION_POPUP_WIDTH,
)
from ...models import NotificationSnapshot
from ...servicios.notificaciones.notifications import NotificationService
from ...ui.disfraces import WindowRole, dress_window
from ...window_identity import (
    TITLE_NOTIFICATION_GROUP,
    anchor_button_geometry,
    compute_popup_top_left,
    configure_interactive_popup,
    configure_toplevel,
    monitor_containing_point,
    popup_window_size,
    reposition_popup,
    reposition_popup_live,
    register_shell_popup,
    schedule_popup_position,
)
from .notification_grouping import grouping_key
from .notification_mini_row import NotificationMiniRow

_SLIDE_TICK_MS = 8


def _ease_in_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4.0 * t * t * t
    u = -2.0 * t + 2.0
    return 1.0 - (u * u * u) / 2.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _measure_widget_height(widget: Gtk.Widget, width: int) -> int:
    widget.set_size_request(width, -1)
    try:
        _minimum, natural = widget.get_preferred_height_for_width(width)
    except Exception:
        _minimum, natural = widget.get_preferred_height()
    return max(1, int(natural))


def _measure_page_height(page_box: Gtk.Box, width: int, spacing: int) -> int:
    """Sum pinned child heights + spacing (deterministic; no live reflow needed)."""
    children = page_box.get_children()
    if not children:
        return 1
    total = 0
    for index, child in enumerate(children):
        total += _measure_widget_height(child, width)
        if index > 0:
            total += spacing
    return max(1, total)


def _fingerprint(snapshots: list[NotificationSnapshot]) -> tuple[int, ...]:
    return tuple(snapshot.id for snapshot in snapshots)


@dataclass
class _BlockPage:
    """One frozen vertical stack of notification rows."""

    root: Gtk.Box
    height: int
    snapshots: list[NotificationSnapshot]
    group_key: tuple[str, ...] | None


class _BlockPager(Gtk.ScrolledWindow):
    """Horizontal pager: stable single page, or FROM|TO slide of whole blocks.

    Never destroys pages — the window owns the cache lifecycle.
    """

    def __init__(
        self,
        width: int,
        spacing: int,
        *,
        on_settled: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.set_shadow_type(Gtk.ShadowType.NONE)
        self.set_overlay_scrolling(True)
        self.get_style_context().add_class("notification-group-pager")

        self._width = max(1, width)
        self._spacing = spacing
        self._on_settled = on_settled
        self._stable: _BlockPage | None = None
        self._from_page: _BlockPage | None = None
        self._to_page: _BlockPage | None = None
        self._tick_id = 0
        self._using_frame_clock = False
        self._anim_start_us = 0
        self._anim_from_x = 0.0
        self._anim_to_x = 0.0
        self._h_from = 1.0
        self._h_to = 1.0

        self._track = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.add(self._track)
        self.set_size_request(self._width, -1)

    @property
    def animating(self) -> bool:
        return self._tick_id != 0 or self.transition_active

    @property
    def stable_page(self) -> _BlockPage | None:
        return self._stable

    @property
    def target_page(self) -> _BlockPage | None:
        """Page we are sliding toward, or the stable page when idle."""
        if self._to_page is not None:
            return self._to_page
        return self._stable

    @property
    def transition_active(self) -> bool:
        return self._to_page is not None and self._from_page is not None

    def snap(self, page: _BlockPage) -> None:
        """Show ``page`` instantly (no animation)."""
        self._stop_tick()
        self._detach_track()
        self._from_page = None
        self._to_page = None
        self._stable = page
        self._attach_page(page)
        self._configure_track(page_count=1)
        self._set_offset(0.0)
        self.set_size_request(self._width, page.height)
        self._track.show_all()

    def begin_transition(self, to_page: _BlockPage) -> bool:
        """Mount FROM|TO without starting a clock (driven by the window timeline)."""
        if self.animating:
            return False

        from_page = self._stable
        if from_page is None:
            self.snap(to_page)
            return False
        if from_page is to_page:
            return False

        self._stop_tick()
        self._detach_track()

        self._from_page = from_page
        self._to_page = to_page
        self._stable = None

        self._attach_page(from_page)
        self._attach_page(to_page)
        self._configure_track(page_count=2)
        self._set_offset(0.0)

        self._h_from = float(from_page.height)
        self._h_to = float(to_page.height)
        self.set_size_request(self._width, int(round(self._h_from)))

        self._anim_from_x = 0.0
        self._anim_to_x = float(self._width)
        self._track.show_all()
        return True

    def apply_progress(self, t: float) -> None:
        """Apply shared eased progress ``t`` in [0, 1] to offset and height."""
        if self._to_page is None:
            return
        x = _lerp(self._anim_from_x, self._anim_to_x, t)
        h = _lerp(self._h_from, self._h_to, t)
        self._set_offset(x)
        self.set_size_request(self._width, max(1, int(round(h))))

    def finish_transition(self) -> None:
        """Settle AFTER the shared timeline reaches t=1."""
        self._settle()

    def transition(self, to_page: _BlockPage) -> None:
        """Legacy entry: begin + own clock (prefer window-driven timeline)."""
        if not self.begin_transition(to_page):
            return
        self._start_animation()

    def _attach_page(self, page: _BlockPage) -> None:
        parent = page.root.get_parent()
        if parent is not None:
            parent.remove(page.root)
        page.root.set_size_request(self._width, page.height)
        page.root.set_hexpand(False)
        page.root.set_vexpand(False)
        self._track.pack_start(page.root, False, False, 0)

    def _detach_track(self) -> None:
        for child in list(self._track.get_children()):
            self._track.remove(child)

    def _configure_track(self, *, page_count: int) -> None:
        count = max(1, page_count)
        self._track.set_size_request(self._width * count, -1)
        adj = self.get_hadjustment()
        page = float(self._width)
        upper = page * count
        value = min(max(adj.get_value(), 0.0), max(0.0, upper - page))
        adj.configure(value, 0.0, upper, 1.0, page, page)

    def _offset(self) -> float:
        return float(self.get_hadjustment().get_value())

    def _set_offset(self, value: float) -> None:
        adj = self.get_hadjustment()
        page = float(self._width)
        pages = 2 if self._to_page is not None and self._from_page is not None else 1
        upper = page * max(1, pages)
        adj.configure(
            min(max(value, 0.0), max(0.0, upper - page)),
            0.0,
            upper,
            1.0,
            page,
            page,
        )

    def _progress(self) -> float:
        duration = float(max(1, NOTIFICATION_GROUP_SLIDE_DURATION_MS))
        elapsed_ms = (GLib.get_monotonic_time() - self._anim_start_us) / 1000.0
        return _ease_in_out_cubic(min(1.0, elapsed_ms / duration))

    def _start_animation(self) -> None:
        if abs(self._anim_from_x - self._anim_to_x) < 0.5:
            self._set_offset(self._anim_to_x)
            self.set_size_request(self._width, int(round(self._h_to)))
            self._settle()
            return
        if NOTIFICATION_GROUP_SLIDE_DURATION_MS <= 0:
            self._set_offset(self._anim_to_x)
            self.set_size_request(self._width, int(round(self._h_to)))
            self._settle()
            return
        self._anim_start_us = GLib.get_monotonic_time()
        self._using_frame_clock = True
        self._tick_id = self.add_tick_callback(self._on_frame)
        if self._tick_id == 0:
            self._using_frame_clock = False
            self._tick_id = GLib.timeout_add(_SLIDE_TICK_MS, self._on_timeout_tick)

    def _stop_tick(self) -> None:
        if not self._tick_id:
            return
        if self._using_frame_clock:
            self.remove_tick_callback(self._tick_id)
        else:
            GLib.source_remove(self._tick_id)
        self._tick_id = 0
        self._using_frame_clock = False

    def _on_frame(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        return self._advance()

    def _on_timeout_tick(self) -> bool:
        return self._advance()

    def _advance(self) -> bool:
        t = self._progress()
        x = _lerp(self._anim_from_x, self._anim_to_x, t)
        h = _lerp(self._h_from, self._h_to, t)
        self._set_offset(x)
        self.set_size_request(self._width, max(1, int(round(h))))
        if t < 1.0:
            return True
        self._tick_id = 0
        self._using_frame_clock = False
        self._settle()
        return False

    def _settle(self) -> None:
        to_page = self._to_page
        self._stop_tick()
        self._detach_track()
        self._from_page = None
        self._to_page = None

        if to_page is None:
            self._stable = None
            self.set_size_request(self._width, -1)
        else:
            self._stable = to_page
            self._attach_page(to_page)
            self._configure_track(page_count=1)
            self._set_offset(0.0)
            self.set_size_request(self._width, to_page.height)
            self._track.show_all()

        if self._on_settled is not None:
            self._on_settled()


class NotificationGroupWindow(Gtk.Window):
    """Stacked notifications: pages cached per parent; slides are coalesced."""

    def __init__(
        self,
        shell_window: Gtk.Window,
        notification_service: NotificationService,
        *,
        on_invoke_action: Callable[[int, str], None],
        on_dismiss: Callable[[int], None],
        on_open_app: Callable[[NotificationSnapshot], None],
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        self._shell_window = shell_window
        self._service = notification_service
        self._on_invoke_action = on_invoke_action
        self._on_dismiss = on_dismiss
        self._on_open_app = on_open_app
        self._fade_source_id = 0
        self._popup_window: Gtk.Window | None = None
        self._anchor_widget: Gtk.Widget | None = None
        self._notifications_position: tuple[int, int, int] | None = None
        self._snapshots: list[NotificationSnapshot] = []
        self._group_key: tuple[str, ...] | None = None
        self._page_cache: dict[tuple[str, ...], _BlockPage] = {}
        self._page_ids: dict[tuple[str, ...], tuple[int, ...]] = {}
        # Latest destination requested while a slide is in progress (coalesce).
        self._queued_snapshots: list[NotificationSnapshot] | None = None
        self._width = NOTIFICATION_POPUP_WIDTH
        self._window_height = NOTIFICATION_POPUP_MAX_HEIGHT + 16
        # Last placed screen coords (X stays fixed while Y animates between parents).
        self._placed_x: int | None = None
        self._placed_y: int | None = None
        # Shared transition timeline (content slide + window Y).
        self._shared_tick_id = 0
        self._shared_using_frame_clock = False
        self._shared_start_us = 0
        self._y_from = 0.0
        self._y_to = 0.0
        self._y_pivot_t = 0.0
        self._y_active = False
        self._content_driving = False
        self._shared_arm_id = 0

        self.set_name("shell-notification-group-window")
        register_shell_popup(self, shell_window)
        configure_toplevel(self, title=TITLE_NOTIFICATION_GROUP)
        configure_interactive_popup(self)
        self.set_default_size(self._width, self._window_height)
        self.set_size_request(self._width, self._window_height)

        self.connect("focus-in-event", self._on_focus_in)
        self.connect("delete-event", self._on_delete)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("notification-group-window-content")
        outer.set_size_request(self._width, self._window_height)
        dress_window(self, WindowRole.NOTIFICATION_GROUP, outer)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_size_request(self._width, NOTIFICATION_POPUP_MAX_HEIGHT)
        scrolled.get_style_context().add_class("notification-group-window-scroll")
        outer.pack_start(scrolled, True, True, 0)

        self._pager = _BlockPager(
            self._width,
            NOTIFICATION_POPUP_LIST_SPACING,
            on_settled=self._on_pager_settled,
        )
        scrolled.add(self._pager)

    def bind_anchor(
        self,
        anchor: Gtk.Widget,
        popup_window: Gtk.Window,
        *,
        notifications_position: tuple[int, int, int] | None = None,
    ) -> None:
        self._anchor_widget = anchor
        self._popup_window = popup_window
        if notifications_position is not None:
            self._notifications_position = notifications_position

    @property
    def snapshot_ids(self) -> tuple[int, ...]:
        return tuple(snapshot.id for snapshot in self._snapshots)

    @property
    def group_key(self) -> tuple[str, ...] | None:
        return self._group_key

    def same_group(self, group_snapshots: list[NotificationSnapshot]) -> bool:
        if not group_snapshots or self._group_key is None:
            return False
        return grouping_key(group_snapshots[0]) == self._group_key

    def preload_groups(self, groups: list[list[NotificationSnapshot]]) -> None:
        """Build/measure every multi-item parent page once (panel open / refresh)."""
        seen: set[tuple[str, ...]] = set()
        for group in groups:
            if len(group) < 2:
                continue
            key = grouping_key(group[0])
            seen.add(key)
            self._page_for(list(group))
        # Drop cache entries for parents that no longer exist in history.
        for key in list(self._page_cache):
            if key not in seen and key != self._group_key:
                self._discard_cached(key)

    def show_group(
        self,
        group_snapshots: list[NotificationSnapshot],
        *,
        animate: bool,
    ) -> None:
        """Navigate to ``group_snapshots`` using cache; coalesce while sliding."""
        new_snapshots = list(group_snapshots)
        new_key = grouping_key(new_snapshots[0]) if new_snapshots else None

        can_animate = bool(
            animate
            and self.get_visible()
            and (self._pager.stable_page is not None or self._pager.animating)
        )

        if not can_animate:
            self._queued_snapshots = None
            self._apply_visible_state(new_snapshots, new_key)
            page = self._page_for(new_snapshots) if new_snapshots else self._empty_page()
            self._pager.snap(page)
            return

        # Already showing / sliding toward this parent with same ids: no-op.
        target = self._pager.target_page
        if (
            target is not None
            and target.group_key == new_key
            and _fingerprint(target.snapshots) == _fingerprint(new_snapshots)
        ):
            self._queued_snapshots = None
            return

        if self._pager.animating:
            # Finish current slide, then go to the latest requested parent.
            self._queued_snapshots = new_snapshots
            return

        page = self._page_for(new_snapshots)
        self._queued_snapshots = None
        self._apply_visible_state(new_snapshots, new_key)
        if self._pager.begin_transition(page):
            self._content_driving = True
            self._arm_shared_timeline()
        else:
            self._content_driving = False

    def set_group(
        self,
        group_snapshots: list[NotificationSnapshot],
        *,
        animate: bool,
    ) -> None:
        """Public swap entry (barra / sync)."""
        self.show_group(group_snapshots, animate=animate)

    # --- Compatibility façade (barra may still call these) ---

    def prepare_transition(
        self,
        group_snapshots: list[NotificationSnapshot],
        *,
        animate: bool,
    ) -> None:
        """No-op preload hint: pages come from cache; show_group does the work."""
        if group_snapshots:
            self._page_for(list(group_snapshots))

    def has_pending_plan_for(self, group_snapshots: list[NotificationSnapshot]) -> bool:
        return False

    def commit_transition(self) -> bool:
        return False

    def cancel_pending_transition(
        self,
        group_snapshots: list[NotificationSnapshot] | None = None,
    ) -> None:
        if group_snapshots is None:
            self._queued_snapshots = None
            return
        if not self._queued_snapshots:
            return
        if not group_snapshots:
            return
        if grouping_key(self._queued_snapshots[0]) == grouping_key(group_snapshots[0]):
            self._queued_snapshots = None

    def sync_from_history(self) -> bool:
        if self._group_key is None:
            return False
        remaining = [
            snapshot
            for snapshot in self._service.history_snapshots
            if grouping_key(snapshot) == self._group_key
        ]
        remaining.sort(key=lambda item: item.timestamp, reverse=True)
        if not remaining:
            self._queued_snapshots = None
            self._apply_visible_state([], None)
            self._pager.snap(self._empty_page())
            return False
        self.show_group(remaining, animate=False)
        return True

    def present_group(self) -> None:
        self.set_opacity(0.0)
        self.show_all()
        self.present()
        if self._fade_source_id:
            GLib.source_remove(self._fade_source_id)
        self._fade_source_id = GLib.timeout_add(16, self._fade_in_tick)
        self._schedule_position()

    def hide_group(self) -> None:
        if self._fade_source_id:
            GLib.source_remove(self._fade_source_id)
            self._fade_source_id = 0
        self._stop_shared_tick()
        self._content_driving = False
        self._y_active = False
        self.hide()
        self.set_opacity(1.0)

    def destroy_group(self) -> None:
        self._queued_snapshots = None
        self._stop_shared_tick()
        self.hide_group()
        self._group_key = None
        self._snapshots = []
        for key in list(self._page_cache):
            self._discard_cached(key)
        self.destroy()

    def position_left_of_popup(
        self,
        anchor: Gtk.Widget,
        popup_window: Gtk.Window,
    ) -> None:
        """Snap instantly beside the history popup, centered on ``anchor``."""
        self.follow_parent(anchor, popup_window, animate=False)

    def follow_parent(
        self,
        anchor: Gtk.Widget,
        popup_window: Gtk.Window,
        *,
        animate: bool,
    ) -> None:
        """Keep X; move Y so the window center tracks the parent row center."""
        self.bind_anchor(
            anchor,
            popup_window,
            notifications_position=getattr(popup_window, "position", None),
        )
        placement = self._compute_placement(anchor, popup_window)
        if placement is None:
            return
        target_x, target_y = placement

        if not animate or not self.get_visible() or self._placed_y is None:
            self._y_active = False
            if self._placed_x is None:
                self._placed_x = target_x
            self._place_window(self._placed_x, target_y, live=False)
            return

        x = self._placed_x if self._placed_x is not None else target_x
        self._placed_x = x
        current_y = self._y_current() if self._y_active else float(self._placed_y)

        if abs(current_y - target_y) < 0.5 and not self._content_driving:
            self._y_active = False
            self._place_window(x, target_y, live=False)
            return

        # Retarget Y on the shared timeline (same start/duration as content).
        self._y_from = current_y
        self._y_to = float(target_y)
        if self._shared_tick_id and self._content_driving:
            self._y_pivot_t = self._shared_raw_t()
        else:
            self._y_pivot_t = 0.0
        self._y_active = True
        self._arm_shared_timeline()

    def _compute_placement(
        self,
        anchor: Gtk.Widget,
        popup_window: Gtk.Window,
    ) -> tuple[int, int] | None:
        """Return (x, y): X beside history popup; Y centered on parent, panel-clamped."""
        group_width = self._width
        gap = 6

        parent_rect = self._resolve_parent_popup_rect(anchor, popup_window)
        if parent_rect is None:
            return None
        notifications_left, notifications_top, notifications_width = parent_rect
        monitor = monitor_containing_point(notifications_left, notifications_top)
        if monitor is None:
            return None

        left = notifications_left - gap - group_width
        notifications_right = notifications_left + notifications_width
        if left < monitor.x:
            left = notifications_right + gap
        left = max(monitor.x, min(left, monitor.x + monitor.width - group_width))

        panel_top, panel_bottom = self._panel_vertical_bounds(
            monitor=monitor,
            notifications_top=notifications_top,
            popup_window=popup_window,
        )
        window_height = self._fit_window_height(panel_top, panel_bottom)

        geometry = anchor_button_geometry(anchor)
        if geometry is None:
            ideal_y = notifications_top
        else:
            parent_center_y = geometry.top + geometry.height // 2
            ideal_y = parent_center_y - window_height // 2

        top = self._clamp_y(ideal_y, window_height, panel_top, panel_bottom)
        return left, top

    def _panel_vertical_bounds(
        self,
        *,
        monitor,
        notifications_top: int,
        popup_window: Gtk.Window,
    ) -> tuple[int, int]:
        """Available vertical band: below the bar / history top, above monitor bottom."""
        del popup_window  # reserved for future padding-aware bounds
        bar_bottom = self._shell_bar_bottom()
        panel_top = max(monitor.y, notifications_top)
        if bar_bottom is not None:
            panel_top = max(panel_top, bar_bottom)
        panel_bottom = monitor.y + monitor.height
        if panel_bottom <= panel_top:
            panel_bottom = panel_top + 1
        return panel_top, panel_bottom

    def _shell_bar_bottom(self) -> int | None:
        shell = self._shell_window
        if not shell.get_realized():
            return None
        gdk_window = shell.get_window()
        if gdk_window is None:
            return None
        origin = gdk_window.get_origin()
        if len(origin) == 3:
            _ok, root_x, root_y = origin
        else:
            root_x, root_y = origin
        height = max(1, int(shell.get_allocated_height()))
        return int(root_y) + height

    def _fit_window_height(self, panel_top: int, panel_bottom: int) -> int:
        available = max(1, panel_bottom - panel_top)
        target = min(self._window_height, available)
        requested = self.get_size_request().height
        if requested != target:
            self.set_size_request(self._width, target)
            self.set_default_size(self._width, target)
        return target

    @staticmethod
    def _clamp_y(
        y: float,
        window_height: int,
        panel_top: int,
        panel_bottom: int,
    ) -> int:
        min_y = panel_top
        max_y = panel_bottom - window_height
        if max_y < min_y:
            return min_y
        return int(max(min_y, min(y, max_y)))

    def _place_window(self, x: int, y: int, *, live: bool) -> None:
        nx, ny = int(x), int(y)
        if (
            live
            and self._placed_x == nx
            and self._placed_y == ny
        ):
            return
        self._placed_x = nx
        self._placed_y = ny
        if live:
            reposition_popup_live(
                self,
                title=TITLE_NOTIFICATION_GROUP,
                x=nx,
                y=ny,
            )
        else:
            reposition_popup(
                self,
                title=TITLE_NOTIFICATION_GROUP,
                x=nx,
                y=ny,
            )

    def _shared_raw_t(self) -> float:
        duration = float(max(1, NOTIFICATION_GROUP_SLIDE_DURATION_MS))
        elapsed_ms = (GLib.get_monotonic_time() - self._shared_start_us) / 1000.0
        return min(1.0, max(0.0, elapsed_ms / duration))

    def _shared_eased_t(self) -> float:
        return _ease_in_out_cubic(self._shared_raw_t())

    def _y_current(self) -> float:
        if not self._y_active:
            return float(self._placed_y or 0)
        t = self._shared_eased_t()
        return self._y_at(t)

    def _y_at(self, t: float) -> float:
        pivot = self._y_pivot_t
        if pivot <= 0.0:
            return _lerp(self._y_from, self._y_to, t)
        if t <= pivot:
            return self._y_from
        span = max(1e-6, 1.0 - pivot)
        u = (t - pivot) / span
        return _lerp(self._y_from, self._y_to, u)

    def _arm_shared_timeline(self) -> None:
        """Start the shared clock on idle so content + Y are armed in the same frame."""
        if self._shared_tick_id or self._shared_arm_id:
            return
        self._shared_arm_id = GLib.idle_add(self._start_armed_shared)

    def _start_armed_shared(self) -> bool:
        self._shared_arm_id = 0
        self._ensure_shared_timeline()
        return False

    def _ensure_shared_timeline(self) -> None:
        if self._shared_tick_id:
            return
        if not self._content_driving and not self._y_active:
            return
        if NOTIFICATION_GROUP_SLIDE_DURATION_MS <= 0:
            self._apply_shared_progress(1.0)
            self._finish_shared_timeline()
            return
        self._shared_start_us = GLib.get_monotonic_time()
        self._shared_using_frame_clock = True
        self._shared_tick_id = self.add_tick_callback(self._on_shared_frame)
        if self._shared_tick_id == 0:
            self._shared_using_frame_clock = False
            self._shared_tick_id = GLib.timeout_add(
                _SLIDE_TICK_MS,
                self._on_shared_timeout,
            )

    def _stop_shared_tick(self) -> None:
        if self._shared_arm_id:
            GLib.source_remove(self._shared_arm_id)
            self._shared_arm_id = 0
        if not self._shared_tick_id:
            return
        if self._shared_using_frame_clock:
            self.remove_tick_callback(self._shared_tick_id)
        else:
            GLib.source_remove(self._shared_tick_id)
        self._shared_tick_id = 0
        self._shared_using_frame_clock = False

    def _on_shared_frame(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        return self._advance_shared()

    def _on_shared_timeout(self) -> bool:
        return self._advance_shared()

    def _advance_shared(self) -> bool:
        raw = self._shared_raw_t()
        t = _ease_in_out_cubic(raw)
        self._apply_shared_progress(t)
        if raw < 1.0:
            return True
        self._shared_tick_id = 0
        self._shared_using_frame_clock = False
        self._finish_shared_timeline()
        return False

    def _apply_shared_progress(self, t: float) -> None:
        if self._content_driving and self._pager.transition_active:
            self._pager.apply_progress(t)
        if self._y_active and self._placed_x is not None:
            y = self._y_at(t)
            self._place_window(self._placed_x, int(round(y)), live=True)

    def _finish_shared_timeline(self) -> None:
        had_content = self._content_driving and self._pager.transition_active
        self._content_driving = False
        if self._y_active and self._placed_x is not None:
            self._place_window(self._placed_x, int(round(self._y_to)), live=False)
        self._y_active = False
        self._y_pivot_t = 0.0
        if had_content:
            self._pager.finish_transition()

    def _on_pager_settled(self) -> None:
        queued = self._queued_snapshots
        self._queued_snapshots = None
        if not queued:
            return
        key = grouping_key(queued[0])
        if key == self._group_key and _fingerprint(queued) == _fingerprint(self._snapshots):
            return
        page = self._page_for(queued)
        self._apply_visible_state(queued, key)
        if self._pager.begin_transition(page):
            self._content_driving = True
            self._arm_shared_timeline()

    def _apply_visible_state(
        self,
        snapshots: list[NotificationSnapshot],
        key: tuple[str, ...] | None,
    ) -> None:
        self._snapshots = list(snapshots)
        self._group_key = key

    def _page_for(self, snapshots: list[NotificationSnapshot]) -> _BlockPage:
        if not snapshots:
            return self._empty_page()
        key = grouping_key(snapshots[0])
        ids = _fingerprint(snapshots)
        cached = self._page_cache.get(key)
        if cached is not None and self._page_ids.get(key) == ids:
            return cached
        if cached is not None:
            self._discard_cached(key)
        page = self._build_page(snapshots)
        self._page_cache[key] = page
        self._page_ids[key] = ids
        return page

    def _empty_page(self) -> _BlockPage:
        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=NOTIFICATION_POPUP_LIST_SPACING,
        )
        root.set_size_request(self._width, 1)
        return _BlockPage(root=root, height=1, snapshots=[], group_key=None)

    def _discard_cached(self, key: tuple[str, ...]) -> None:
        page = self._page_cache.pop(key, None)
        self._page_ids.pop(key, None)
        if page is None:
            return
        parent = page.root.get_parent()
        if parent is not None:
            parent.remove(page.root)
        page.root.destroy()

    def _build_page(self, snapshots: list[NotificationSnapshot]) -> _BlockPage:
        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=NOTIFICATION_POPUP_LIST_SPACING,
        )
        root.get_style_context().add_class("notification-group-window-list")
        root.set_size_request(self._width, -1)

        for snapshot in snapshots:
            row = self._make_row(snapshot)
            root.pack_start(row, False, False, 0)

        root.show_all()
        height = _measure_page_height(
            root,
            self._width,
            NOTIFICATION_POPUP_LIST_SPACING,
        )
        root.set_size_request(self._width, height)

        group_key = grouping_key(snapshots[0]) if snapshots else None
        return _BlockPage(
            root=root,
            height=height,
            snapshots=list(snapshots),
            group_key=group_key,
        )

    def _make_row(self, snapshot: NotificationSnapshot) -> NotificationMiniRow:
        row = NotificationMiniRow(snapshot, on_dismiss=self._on_dismiss)
        row.connect("button-press-event", self._on_row_clicked, snapshot)
        return row

    def _schedule_position(self) -> None:
        if self._popup_window is None or self._anchor_widget is None:
            return
        if self.get_mapped():
            self.position_left_of_popup(self._anchor_widget, self._popup_window)
        else:
            self.connect("map-event", self._on_first_map)
        schedule_popup_position(self._position_from_anchor_after_popup_map)

    def _resolve_parent_popup_rect(
        self,
        anchor: Gtk.Widget,
        popup_window: Gtk.Window,
    ) -> tuple[int, int, int] | None:
        if self._notifications_position is not None:
            return self._notifications_position

        geometry = anchor_button_geometry(anchor)
        if geometry is None:
            return None
        width, _height = popup_window_size(popup_window)
        monitor = monitor_containing_point(geometry.center_x, geometry.bottom)
        left, top = compute_popup_top_left(
            button_center_x=geometry.center_x,
            button_bottom=geometry.bottom,
            popup_width=width,
            popup_height=NOTIFICATION_POPUP_MAX_HEIGHT,
            offset=NOTIFICATION_POPUP_OFFSET,
            monitor=monitor,
        )
        self._notifications_position = (left, top, width)
        return self._notifications_position

    def _position_from_anchor_after_popup_map(self) -> bool:
        if self._anchor_widget is not None and self._popup_window is not None:
            self.position_left_of_popup(self._anchor_widget, self._popup_window)
        return False

    def _on_first_map(self, _widget: Gtk.Widget, _event: Gdk.EventAny) -> bool:
        self.disconnect_by_func(self._on_first_map)
        if self._anchor_widget is not None and self._popup_window is not None:
            self.position_left_of_popup(self._anchor_widget, self._popup_window)
        return False

    def _fade_in_tick(self) -> bool:
        next_opacity = min(1.0, self.get_opacity() + 0.20)
        self.set_opacity(next_opacity)
        if next_opacity >= 1.0:
            self._fade_source_id = 0
            return False
        return True

    def _on_focus_in(self, _widget: Gtk.Widget, _event: Gdk.EventFocus) -> bool:
        return False

    def _on_delete(self, _widget: Gtk.Widget, _event: Gdk.Event) -> bool:
        GLib.idle_add(self.hide_group)
        return True

    def _on_row_clicked(
        self,
        _widget: Gtk.Widget,
        event: Gdk.EventButton,
        snapshot: NotificationSnapshot,
    ) -> bool:
        if event.button != 1:
            return False
        default = next(
            (action.key for action in snapshot.actions if action.key == "default"),
            None,
        )
        if default is None and snapshot.actions:
            default = snapshot.actions[0].key
        if default is not None:
            self._on_invoke_action(snapshot.id, default)
        self._on_open_app(snapshot)
        GLib.idle_add(self.hide_group)
        return True
