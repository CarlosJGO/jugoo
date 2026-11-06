"""Pure geometry for the radial launcher overlay."""

from __future__ import annotations

import math
from dataclasses import dataclass

_EDGE_MARGIN = 16
_CENTER_RADIUS = 36
_PREFERRED_RADIUS_RATIO = 0.11
_ICON_RADIUS_RATIO = 0.68
_ICON_GAP = 5
_MAX_SECTORS_PER_RING = 12
_RING_THICKNESS = 38
_CENTER_LABEL_HALF_WIDTH = 78
_EMPTY_WIDTH = 200
_EMPTY_HEIGHT = 84


def _preferred_icon_size(count: int) -> float:
    if count <= 8:
        return 28
    if count <= 16:
        return 25
    if count <= 24:
        return 22
    if count <= 40:
        return 19
    if count <= 56:
        return 16
    return 12.5


@dataclass(frozen=True)
class RadialPosition:
    center_x: float
    center_y: float
    size: float
    ring: int
    start_angle: float
    end_angle: float
    inner_radius: float
    outer_radius: float


@dataclass(frozen=True)
class RadialLayout:
    center_x: float
    center_y: float
    origin_x: float
    origin_y: float
    positions: tuple[RadialPosition, ...]
    half_width: float
    half_height: float
    node_size: float
    ring_count: int


def radial_layout(
    count: int,
    width: int,
    height: int,
    *,
    origin: tuple[float, float] | None = None,
) -> RadialLayout:
    """Lay out radial app sectors around ``origin`` and clamp the full menu.

    ``origin`` and all returned coordinates use monitor-local logical pixels.
    """
    if count < 0:
        raise ValueError("count must not be negative")
    if width <= 0 or height <= 0:
        raise ValueError("monitor dimensions must be positive")

    width_f, height_f = float(width), float(height)
    if origin is None:
        origin_x, origin_y = width_f / 2, height_f / 2
    else:
        origin_x, origin_y = map(float, origin)
        if not math.isfinite(origin_x) or not math.isfinite(origin_y):
            raise ValueError("origin coordinates must be finite")
        origin_x = min(width_f, max(0.0, origin_x))
        origin_y = min(height_f, max(0.0, origin_y))

    if count == 0:
        empty_width = min(_EMPTY_WIDTH, max(2.0, width_f - 2 * _EDGE_MARGIN))
        half_width = max(1.0, empty_width / 2)
        empty_bottom = min(_EMPTY_HEIGHT / 2, height_f / 2 - _EDGE_MARGIN)
        center_top = min(_CENTER_RADIUS, height_f / 2 - _EDGE_MARGIN)
        offsets = (-half_width, half_width, -center_top, empty_bottom)
        return _clamp_layout(
            (),
            width_f,
            height_f,
            origin_x,
            origin_y,
            offsets,
            node_size=0.0,
            ring_count=0,
        )

    preferred_icon_size = _preferred_icon_size(count)
    max_radius = min(width_f, height_f) / 2 - _EDGE_MARGIN
    ring_count = math.ceil(count / _MAX_SECTORS_PER_RING)
    preferred_ratio = (
        0.07 if count <= 2 else 0.085 if count <= 4 else _PREFERRED_RADIUS_RATIO
    )
    preferred_radius = min(width_f, height_f) * preferred_ratio
    ring_counts = [count // ring_count] * ring_count
    for index in range(count % ring_count):
        ring_counts[index] += 1
    first_ring_radius = max(
        preferred_radius,
        (_CENTER_RADIUS + _ICON_GAP + preferred_icon_size / 2)
        / _ICON_RADIUS_RATIO,
    )
    min_ring_thickness = max(24.0, preferred_icon_size + _ICON_GAP * 2)
    if ring_count > 1:
        max_first_radius = max_radius - (ring_count - 1) * min_ring_thickness
        if max_first_radius < first_ring_radius:
            raise ValueError(
                f"{count} applications do not fit on a {width}x{height} monitor"
            )
        first_ring_radius = min(first_ring_radius, max_first_radius)
        ring_thickness = min(
            _RING_THICKNESS,
            (max_radius - first_ring_radius) / (ring_count - 1),
        )
    else:
        ring_thickness = 0.0
    if ring_thickness and ring_thickness < min_ring_thickness:
        raise ValueError(
            f"{count} applications do not fit on a {width}x{height} monitor"
        )

    positions: list[RadialPosition] = []
    ring_inner_radius = 0.0
    for ring_index, ring_app_count in enumerate(ring_counts):
        ring_outer_radius = first_ring_radius + ring_index * ring_thickness
        sector_angle = math.tau / ring_app_count
        icon_radius = (
            ring_outer_radius * _ICON_RADIUS_RATIO
            if ring_index == 0
            else (ring_inner_radius + ring_outer_radius) / 2
        )
        half_angle_sine = math.sin(math.pi / ring_app_count)
        icon_size = min(
            preferred_icon_size,
            max(
                1.0,
                2 * icon_radius * half_angle_sine - _ICON_GAP,
            ),
        )
        if ring_index > 0:
            icon_size = min(
                icon_size,
                max(
                    1.0,
                    ring_outer_radius - ring_inner_radius - _ICON_GAP * 2,
                ),
            )
        for item_index in range(ring_app_count):
            angle = -math.pi / 2 + sector_angle * item_index
            positions.append(
                RadialPosition(
                    math.cos(angle) * icon_radius,
                    math.sin(angle) * icon_radius,
                    icon_size,
                    ring_index,
                    angle - sector_angle / 2,
                    angle + sector_angle / 2,
                    ring_inner_radius,
                    ring_outer_radius,
                )
            )
        ring_inner_radius = ring_outer_radius

    radial_extent = max(ring_inner_radius, _CENTER_LABEL_HALF_WIDTH)
    bounds = (-radial_extent, radial_extent, -radial_extent, radial_extent)
    return _clamp_layout(
        tuple(positions),
        width_f,
        height_f,
        origin_x,
        origin_y,
        bounds,
        node_size=preferred_icon_size,
        ring_count=ring_count,
    )


def _clamp_layout(
    positions: tuple[RadialPosition, ...],
    width: float,
    height: float,
    origin_x: float,
    origin_y: float,
    bounds: tuple[float, float, float, float],
    *,
    node_size: float,
    ring_count: int,
) -> RadialLayout:
    left, right, top, bottom = bounds
    min_center_x, max_center_x = -left, width - right
    min_center_y, max_center_y = -top, height - bottom
    if min_center_x > max_center_x or min_center_y > max_center_y:
        raise ValueError("menu geometry exceeds monitor bounds")
    center_x = min(max(origin_x, min_center_x), max_center_x)
    center_y = min(max(origin_y, min_center_y), max_center_y)
    translated = tuple(
        RadialPosition(
            center_x + position.center_x,
            center_y + position.center_y,
            position.size,
            position.ring,
            position.start_angle,
            position.end_angle,
            position.inner_radius,
            position.outer_radius,
        )
        for position in positions
    )
    return RadialLayout(
        center_x=center_x,
        center_y=center_y,
        origin_x=origin_x,
        origin_y=origin_y,
        positions=translated,
        half_width=max(abs(left), abs(right)),
        half_height=max(abs(top), abs(bottom)),
        node_size=node_size,
        ring_count=ring_count,
    )
