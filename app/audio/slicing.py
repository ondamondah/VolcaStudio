import numpy as np
import soundfile as sf



def beat_markers(frames, rate, first_beat, bpm, beats_per_slice):
    step = rate * 60 / bpm * beats_per_slice
    count = max(0, int((frames - first_beat) / step))
    markers = [first_beat + round(index * step) for index in range(count + 1)]
    return sorted({marker for marker in markers if 0 < marker < frames})



def estimate_beat_grid(path, *, tempo_hint=None):
    """Return a proposed BPM and the frame of the first detected beat."""
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    if len(audio) < rate * 1.2:
        return None
    audio -= np.mean(audio, axis=0)
    peak = float(np.max(np.abs(audio)))
    if peak < 1 / 32768:
        return None
    hop = max(1, round(rate * 0.004))
    score = _spectral_rise(audio, rate, max(8, round(rate * 0.024)), hop, peak)
    threshold = max(0.012, float(np.percentile(score, 98)) * 0.16)
    attacks = np.flatnonzero(
        (score[1:-1] >= threshold) & (score[1:-1] > score[:-2]) & (score[1:-1] >= score[2:])
    ) + 1
    if len(attacks) < 4:
        return None

    pulses = np.zeros_like(score)
    pulses[attacks] = score[attacks] ** 0.7
    # A short smoothing window tolerates timing variations across live hits.
    pulses = np.convolve(pulses, [0.25, 0.5, 1, 0.5, 0.25], mode="same")
    fft_size = 1 << (2 * len(pulses) - 1).bit_length()
    correlation = np.fft.irfft(np.abs(np.fft.rfft(pulses, fft_size)) ** 2)
    minimum = max(2, round(rate * 60 / (200 * hop)))
    maximum = min(round(rate * 60 / (55 * hop)), len(pulses) // 3)
    if maximum <= minimum:
        return None
    lags = np.arange(minimum, maximum + 1)
    local = correlation[lags]
    peaks = lags[(local >= correlation[lags - 1]) & (local >= correlation[lags + 1])]
    if not len(peaks):
        return None
    strengths = correlation[peaks] / correlation[0]
    if strengths.max() < 0.15:
        return None
    positions = np.array([_attack_start(audio, int(i) * hop, rate, peak) for i in attacks])
    weights = score[attacks] ** 0.7
    fitted = []
    strongest = strengths.max()
    for lag in peaks[np.argsort(strengths)[::-1][:6]]:
        strength = correlation[lag] / correlation[0]
        if strength < 0.55 * strongest:
            continue
        left, center, right = correlation[lag - 1:lag + 2]
        curvature = 2 * center - left - right
        period = hop * (lag + (right - left) / (2 * curvature) if curvature > 0 else lag)
        fit = _fit_grid(positions, weights, period)
        if fit is None:
            continue
        period, phase, cycle, support = fit
        bpm = 60 * rate / period
        preference = np.exp(-0.18 * abs(np.log2(bpm / 115)))
        if tempo_hint:
            preference *= 1 + 0.60 * np.exp(-0.5 * (np.log2(bpm / tempo_hint) / 0.055) ** 2)
        fitted.append((support * (strength / strongest) ** 0.35 * preference, period, phase, cycle))
    if not fitted:
        return None
    _, period, phase, first_cycle = max(fitted)
    # Use the first supported beat instead of extending the grid into a quiet lead-in.
    first = round(phase + first_cycle * period)
    preceding = round(first - period)
    if abs(preceding) <= 0.04 * rate and np.max(np.abs(audio[:max(1, rate // 100)])) > peak * 0.1:
        first = 0
    if abs(first) < 0.012 * rate:
        first = 0
    return round(float(60 * rate / period), 2), max(0, first)



def _fit_grid(positions, weights, period):
    # Refine both period and phase; a small BPM error can drift across a long loop.
    bins = 96
    offsets = np.arange(-5, 6)
    kernel = 1 - np.abs(offsets) / 6
    best_support = 0.0
    for candidate in period * np.linspace(0.96, 1.04, 101):
        indices = np.rint(positions / candidate * bins).astype(int) % bins
        histogram = np.bincount(indices, weights=weights, minlength=bins)
        support = sum(weight * np.roll(histogram, offset) for offset, weight in zip(offsets, kernel))
        strongest = int(np.argmax(support))
        if support[strongest] > best_support:
            best_support = support[strongest]
            best_period = candidate
            phase = strongest * candidate / bins
    cycles = np.rint((positions - phase) / best_period).astype(int)
    aligned = np.abs(positions - (phase + cycles * best_period)) < 0.045 * best_period
    beats = np.unique(cycles[aligned])
    if len(beats) < 3:
        return None
    period, phase = np.polyfit(cycles[aligned], positions[aligned], 1, w=np.sqrt(weights[aligned]))
    coverage = len(beats) / (beats[-1] - beats[0] + 1)
    return period, phase, beats[0], best_support * coverage ** 0.5

def transient_markers(path, *, sensitivity="balanced"):
    """Locate attacks on the original PCM frame grid."""
    thresholds = {
        "strong": (0.35, 1.65, 0.090),
        "balanced": (0.12, 1.35, 0.055),
        "detailed": (0.06, 1.20, 0.035),
    }
    strong_fraction, local_multiplier, separation = thresholds[sensitivity]
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    minimum = max(1, round(rate * separation))
    if len(audio) < 2 * minimum:
        return []
    # Remove DC per channel and retain stereo energy without cancelling opposite phases.
    audio -= np.mean(audio, axis=0)
    peak = float(np.max(np.abs(audio)))
    if peak < 1 / 32768:
        return []
    hop = max(1, round(rate * 0.004))
    window = max(8, round(rate * 0.024))
    score = _spectral_rise(audio, rate, window, hop, peak)
    if len(score) < 3 or score.max() <= 0:
        return []

    radius = max(1, round(0.33 * rate / hop))
    neighborhood = np.lib.stride_tricks.sliding_window_view(
        np.pad(score, radius, mode="edge"), 2 * radius + 1,
    )
    floor = np.percentile(neighborhood, 90, axis=1)
    strongest = float(np.percentile(score[score > 0], 98))
    threshold = np.maximum(max(0.009, strong_fraction * strongest), local_multiplier * floor)
    candidates = np.flatnonzero(
        (score[1:-1] >= threshold[1:-1])
        & (score[1:-1] > score[:-2])
        & (score[1:-1] >= score[2:])
    ) + 1

    selected = []
    for index in sorted(candidates, key=lambda index: -score[index]):
        frame = _attack_start(audio, int(index) * hop, rate, peak)
        if minimum <= frame <= len(audio) - minimum and all(
            abs(frame - prior) >= minimum for prior in selected
        ):
            selected.append(frame)
            if len(selected) == 31:
                break
    return sorted(selected)


def _spectral_rise(audio, rate, window, hop, peak):
    padded = np.pad(audio, ((window // 2, window // 2), (0, 0)))
    frames = np.lib.stride_tricks.sliding_window_view(padded, window, axis=0)[::hop]
    weights = np.hanning(window).astype("float32")
    frequencies = np.fft.rfftfreq(window, 1 / rate)
    edges = (0, 180, 700, 2500, 6500, np.inf)
    bands = [(frequencies >= low) & (frequencies < high) for low, high in zip(edges, edges[1:])]
    score = np.zeros(len(frames))
    previous = None
    for offset in range(0, len(frames), 256):
        spectrum = np.fft.rfft(frames[offset:offset + 256] * weights, axis=-1)
        magnitude = np.sqrt(np.mean(np.abs(spectrum) ** 2, axis=1)) / weights.sum()
        spectrum = np.log1p(magnitude * (32 / peak))
        spectrum = np.vstack((previous if previous is not None else spectrum[0], spectrum))
        # Compare against neighboring bins so small pitch shifts score less like new hits.
        prior = np.pad(spectrum[:-1], ((0, 0), (1, 1)), mode="edge")
        neighbor_max = np.maximum(np.maximum(prior[:, :-2], prior[:, 1:-1]), prior[:, 2:])
        rise = np.maximum(spectrum[1:] - neighbor_max, 0)
        band_scores = [np.sqrt(np.mean(rise[:, bins] ** 2, axis=1)) for bins in bands if bins.any()]
        score[offset:offset + len(rise)] = np.max(band_scores, axis=0) + 0.4 * np.mean(band_scores, axis=0)
        previous = spectrum[-1]
    return score


def _attack_start(audio, center, rate, peak):
    step = max(1, round(rate * 0.001))
    left = max(0, center - 18 * step)
    right = min(len(audio), center + 12 * step)
    frames = (right - left) // step
    if frames < 3:
        return min(center, len(audio) - 1)
    samples = audio[left:left + frames * step].reshape(frames, step, -1)
    energy = np.sqrt(np.mean(samples ** 2, axis=(1, 2)))
    rise = np.diff(energy)
    weight = 1 - 0.4 * np.abs(np.arange(len(rise)) * step + left - center) / (18 * step)
    attack = int(np.argmax(rise * weight))
    if rise[attack] < 0.008 * peak:
        return max(0, center - 2 * step)
    level = energy[attack] + rise[attack] * 0.20
    start = left + (attack + 1) * step
    crossing = np.flatnonzero(np.max(np.abs(audio[start:start + step]), axis=1) >= level)
    return max(0, start + (int(crossing[0]) if crossing.size else 0) - step)
