"""One-shot pulse envelopes for the Tasks icon. No GTK."""

from __future__ import annotations

import math

from ...servicios.tareas.vigilancia.eventos import KIND_AI_REMINDER, KIND_REMINDER

HEARTBEAT_DURATION_MS = 260
REMINDER_DURATION_MS = 280
HEARTBEAT_AMPLITUDE = 0.06
REMINDER_AMPLITUDE = 0.12
PULSE_TICK_MS = 16


def pulse_duration_ms(kind: str) -> int:
    if kind in {KIND_REMINDER, KIND_AI_REMINDER}:
        return REMINDER_DURATION_MS
    return HEARTBEAT_DURATION_MS


def pulse_amplitude(kind: str) -> float:
    if kind in {KIND_REMINDER, KIND_AI_REMINDER}:
        return REMINDER_AMPLITUDE
    return HEARTBEAT_AMPLITUDE


def pulse_progress_scale(progress: float, amplitude: float) -> float:
    """Single sine bump: 1 → 1+amplitude → 1 over progress 0..1."""
    t = max(0.0, min(1.0, progress))
    return 1.0 + amplitude * math.sin(math.pi * t)
