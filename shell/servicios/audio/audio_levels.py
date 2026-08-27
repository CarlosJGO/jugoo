"""Pure helpers: PCM → FFT bands → smoothed bars + frequency colors."""

from __future__ import annotations

import math
import struct
from typing import Sequence

from ...models import MediaSnapshot

_VISIBILITY_FLOOR = 0.012
_FFT_SIZE = 512
_F_MIN_HZ = 40.0
_ATTACK = 0.72
_RELEASE = 0.18
_PEAK_FALL = 0.045
_PEAK_HOLD = 0.02


def visualizer_should_sample(media_snapshot: MediaSnapshot) -> bool:
    """True when MPRIS reports active playback for the selected player."""
    active = media_snapshot.active
    return active is not None and active.status == "playing"


def visualizer_is_visible(media_snapshot: MediaSnapshot) -> bool:
    """Sampling / visibility gate follows MPRIS Playing."""
    return visualizer_should_sample(media_snapshot)


def empty_bars(bar_count: int) -> tuple[float, ...]:
    return tuple(0.0 for _ in range(bar_count))


def empty_colors(bar_count: int) -> tuple[tuple[float, float, float, float], ...]:
    return tuple((0.0, 0.0, 0.0, 0.0) for _ in range(bar_count))


def decay_bars(
    bars: Sequence[float],
    *,
    factor: float = 0.78,
) -> tuple[float, ...]:
    return tuple(max(0.0, float(value) * factor) for value in bars)


def decay_peaks(
    peaks: Sequence[float],
    *,
    fall: float = _PEAK_FALL,
) -> tuple[float, ...]:
    return tuple(max(0.0, float(value) - fall) for value in peaks)


def pcm_s16le_mono_samples(pcm: bytes) -> tuple[int, ...]:
    if len(pcm) < 2:
        return ()
    sample_count = len(pcm) // 2
    return struct.unpack(f"<{sample_count}h", pcm[: sample_count * 2])


def pcm_rms(pcm: bytes) -> float:
    samples = pcm_s16le_mono_samples(pcm)
    if not samples:
        return 0.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return math.sqrt(mean_square)


def bars_have_energy(bars: Sequence[float]) -> bool:
    return any(float(value) > _VISIBILITY_FLOOR for value in bars)


def logarithmic_band_centers(
    bar_count: int,
    *,
    sample_rate: int,
    f_min: float = _F_MIN_HZ,
) -> tuple[float, ...]:
    """Geometric-mean frequencies for ``bar_count`` log-spaced bands."""
    if bar_count <= 0:
        return ()
    f_max = min(sample_rate * 0.48, 14_000.0)
    if bar_count == 1:
        return (math.sqrt(f_min * f_max),)
    ratio = (f_max / f_min) ** (1.0 / bar_count)
    centers: list[float] = []
    low = f_min
    for _ in range(bar_count):
        high = low * ratio
        centers.append(math.sqrt(low * high))
        low = high
    return tuple(centers)


def frequency_to_rgba(
    freq_hz: float,
    level: float,
    *,
    f_min: float = _F_MIN_HZ,
    f_max: float = 12_000.0,
) -> tuple[float, float, float, float]:
    """Map band frequency → hue and amplitude → saturation/value/alpha."""
    clamped = max(f_min, min(f_max, max(1.0, freq_hz)))
    t = (math.log(clamped) - math.log(f_min)) / max(1e-9, math.log(f_max) - math.log(f_min))
    t = max(0.0, min(1.0, t))
    # Graves → red/orange (hue ~0–0.08), medios → yellow/green/cyan, agudos → blue/violet.
    hue = t * 0.78
    amplitude = max(0.0, min(1.0, level))
    saturation = 0.55 + 0.40 * amplitude
    value = 0.45 + 0.50 * amplitude
    alpha = 0.10 + 0.32 * (amplitude**0.85)
    red, green, blue = _hsv_to_rgb(hue, saturation, value)
    return (red, green, blue, alpha)


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    if s <= 0.0:
        return (v, v, v)
    sector = (h % 1.0) * 6.0
    index = int(sector)
    frac = sector - index
    p = v * (1.0 - s)
    q = v * (1.0 - s * frac)
    t = v * (1.0 - s * (1.0 - frac))
    if index == 0:
        return (v, t, p)
    if index == 1:
        return (q, v, p)
    if index == 2:
        return (p, v, t)
    if index == 3:
        return (p, q, v)
    if index == 4:
        return (t, p, v)
    return (v, p, q)


def _hann_window(size: int) -> tuple[float, ...]:
    if size <= 1:
        return (1.0,)
    return tuple(
        0.5 - 0.5 * math.cos(2.0 * math.pi * index / (size - 1))
        for index in range(size)
    )


def _bit_reverse_permute(real: list[float], imag: list[float]) -> None:
    n = len(real)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            real[i], real[j] = real[j], real[i]
            imag[i], imag[j] = imag[j], imag[i]


def fft_magnitudes(samples: Sequence[float]) -> tuple[float, ...]:
    """Radix-2 FFT magnitude spectrum for a real window (length must be power of 2)."""
    n = len(samples)
    if n == 0 or n & (n - 1):
        raise ValueError("fft_magnitudes requires a non-empty power-of-two length")
    real = list(samples)
    imag = [0.0] * n
    _bit_reverse_permute(real, imag)

    length = 2
    while length <= n:
        angle = -2.0 * math.pi / length
        w_len_re = math.cos(angle)
        w_len_im = math.sin(angle)
        for start in range(0, n, length):
            w_re = 1.0
            w_im = 0.0
            half = length // 2
            for k in range(half):
                i = start + k
                j = i + half
                even_re = real[i]
                even_im = imag[i]
                odd_re = w_re * real[j] - w_im * imag[j]
                odd_im = w_re * imag[j] + w_im * real[j]
                real[i] = even_re + odd_re
                imag[i] = even_im + odd_im
                real[j] = even_re - odd_re
                imag[j] = even_im - odd_im
                next_w_re = w_re * w_len_re - w_im * w_len_im
                w_im = w_re * w_len_im + w_im * w_len_re
                w_re = next_w_re
        length <<= 1

    half = n // 2
    scale = 2.0 / n
    return tuple(math.hypot(real[index], imag[index]) * scale for index in range(half))


def band_energies_from_magnitudes(
    magnitudes: Sequence[float],
    *,
    bar_count: int,
    sample_rate: int,
    f_min: float = _F_MIN_HZ,
) -> tuple[float, ...]:
    """Collapse FFT bins into logarithmic frequency bands."""
    if bar_count <= 0 or not magnitudes:
        return empty_bars(max(0, bar_count))

    bin_count = len(magnitudes)
    hz_per_bin = sample_rate / (2.0 * bin_count)
    f_max = min(sample_rate * 0.48, hz_per_bin * (bin_count - 1))
    ratio = (f_max / f_min) ** (1.0 / bar_count)
    energies: list[float] = []
    low = f_min
    for _ in range(bar_count):
        high = min(f_max, low * ratio)
        start_bin = max(1, int(low / hz_per_bin))
        end_bin = min(bin_count, max(start_bin + 1, int(math.ceil(high / hz_per_bin))))
        segment = magnitudes[start_bin:end_bin]
        if not segment:
            energies.append(0.0)
        else:
            # Mean of squared magnitudes → perceptual loudness with mild compression.
            power = sum(value * value for value in segment) / len(segment)
            energies.append(math.sqrt(power))
        low = high
    return tuple(energies)


def _level_from_band_energy(energy: float, *, sensitivity: float = 0.008) -> float:
    normalized = energy / max(1e-9, sensitivity)
    # Mild compression keeps quiet material visible without clipping everything to 1.
    return max(0.0, min(1.0, normalized**0.38))


def smooth_levels(
    target: Sequence[float],
    previous: Sequence[float],
    *,
    attack: float = _ATTACK,
    release: float = _RELEASE,
) -> tuple[float, ...]:
    count = len(target)
    prev = list(previous) + [0.0] * max(0, count - len(previous))
    out: list[float] = []
    for index in range(count):
        level = float(target[index])
        prior = float(prev[index])
        if level >= prior:
            smoothed = prior * (1.0 - attack) + level * attack
        else:
            smoothed = prior * (1.0 - release) + level * release
        out.append(max(0.0, min(1.0, smoothed)))
    return tuple(out)


def update_peaks(
    bars: Sequence[float],
    previous_peaks: Sequence[float],
    *,
    fall: float = _PEAK_FALL,
    hold: float = _PEAK_HOLD,
) -> tuple[float, ...]:
    count = len(bars)
    prev = list(previous_peaks) + [0.0] * max(0, count - len(previous_peaks))
    peaks: list[float] = []
    for index in range(count):
        level = float(bars[index])
        peak = float(prev[index])
        if level >= peak:
            peaks.append(level)
        else:
            peaks.append(max(level, peak - fall, 0.0) if peak - level > hold else peak)
    return tuple(max(0.0, min(1.0, value)) for value in peaks)


def compute_bars_from_pcm(
    pcm: bytes,
    *,
    bar_count: int,
    previous: Sequence[float],
    sample_rate: int = 16_000,
    previous_peaks: Sequence[float] | None = None,
) -> tuple[float, ...]:
    """Compatibility wrapper: return only smoothed bar heights."""
    bars, _peaks, _colors = compute_spectrum_frame(
        pcm,
        bar_count=bar_count,
        previous_bars=previous,
        previous_peaks=previous_peaks or empty_bars(bar_count),
        sample_rate=sample_rate,
    )
    return bars


def compute_spectrum_frame(
    pcm: bytes,
    *,
    bar_count: int,
    previous_bars: Sequence[float],
    previous_peaks: Sequence[float],
    sample_rate: int = 16_000,
) -> tuple[
    tuple[float, ...],
    tuple[float, ...],
    tuple[tuple[float, float, float, float], ...],
]:
    """PCM → FFT → log bands → attack/decay bars, peaks, and RGBA colors."""
    samples = pcm_s16le_mono_samples(pcm)
    if len(samples) < 32:
        bars = decay_bars(previous_bars)
        peaks = decay_peaks(previous_peaks)
        centers = logarithmic_band_centers(bar_count, sample_rate=sample_rate)
        colors = tuple(frequency_to_rgba(centers[index], bars[index]) for index in range(bar_count))
        return bars, peaks, colors

    window_size = _FFT_SIZE
    if len(samples) < window_size:
        padded = list(samples) + [0] * (window_size - len(samples))
    else:
        padded = list(samples[-window_size:])

    window = _hann_window(window_size)
    framed = [padded[index] / 32768.0 * window[index] for index in range(window_size)]
    magnitudes = fft_magnitudes(framed)
    energies = band_energies_from_magnitudes(
        magnitudes,
        bar_count=bar_count,
        sample_rate=sample_rate,
    )
    targets = tuple(_level_from_band_energy(energy) for energy in energies)
    bars = smooth_levels(targets, previous_bars)
    peaks = update_peaks(bars, previous_peaks)
    centers = logarithmic_band_centers(bar_count, sample_rate=sample_rate)
    colors = tuple(frequency_to_rgba(centers[index], bars[index]) for index in range(bar_count))
    return bars, peaks, colors


def merge_visualizer_snapshot(
    media_snapshot: MediaSnapshot,
    bars: Sequence[float],
    *,
    bar_count: int,
    colors: Sequence[tuple[float, float, float, float]] | None = None,
    peaks: Sequence[float] | None = None,
) -> tuple[bool, tuple[float, ...], tuple[float, ...], tuple[tuple[float, float, float, float], ...]]:
    normalized = tuple(
        float(bars[index]) if index < len(bars) else 0.0 for index in range(bar_count)
    )
    peak_values = tuple(
        float(peaks[index]) if peaks is not None and index < len(peaks) else normalized[index]
        for index in range(bar_count)
    )
    if colors is None:
        centers = logarithmic_band_centers(bar_count, sample_rate=16_000)
        color_values = tuple(
            frequency_to_rgba(centers[index], normalized[index]) for index in range(bar_count)
        )
    else:
        color_values = tuple(
            colors[index] if index < len(colors) else (0.0, 0.0, 0.0, 0.0)
            for index in range(bar_count)
        )

    playing = visualizer_should_sample(media_snapshot)
    if playing:
        return True, normalized, peak_values, color_values
    if bars_have_energy(normalized):
        return True, normalized, peak_values, color_values
    return False, empty_bars(bar_count), empty_bars(bar_count), empty_colors(bar_count)
