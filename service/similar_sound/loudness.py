"""Loudness of a live stream the way broadcast meters (and Youlean) measure it: ITU-R BS.1770-4 / EBU R128.

Interface: Loudness(rate).push(block) takes the next block of audio, (frames, channels) float32, and returns
  {"momentary": LUFS over the last 0.4 s,   "short": LUFS over the last 3 s,
   "integrated": gated LUFS since the start, or None until there is something above the gate,
   "lra": loudness range in LU, or None until there are two short-term readings,
   "true_peak": highest true peak so far in dBTP (4x oversampled),   "plr": true_peak - integrated, or None,
   "hist": counts of short-term readings per 1 LU bin from HIST_LO up (what the histogram draws)}
One instance is one listening session: a new one starts everything from zero. Memory grows by ~10 floats a second.
"""
from __future__ import annotations

from collections import deque

import numpy as np
from scipy.signal import resample_poly, sosfilt

FLOOR = -120.0
ABS_GATE = -70.0                  # blocks quieter than this never count (BS.1770 absolute gate)
HIST_LO, HIST_HI = -60, 0
HIST_BINS = HIST_HI - HIST_LO
OFFSET = -0.691                   # makes a full-scale 997 Hz sine in one channel read -3.01 LUFS
TP_PAD = 24                       # samples of context the oversampling filter needs on each side of what it measures
OVERSAMPLE = 4


def _k_weighting(rate: int) -> np.ndarray:
    """The two-stage K filter (a high shelf, then a ~38 Hz high-pass) as second-order sections, for any sample rate."""
    k = np.tan(np.pi * 1681.9744509555319 / rate)
    q, vh = 0.7071752369554196, 10 ** (3.999843853973347 / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
             1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    k = np.tan(np.pi * 38.13547087602444 / rate)
    q = 0.5003270373238773
    a0 = 1 + k / q + k * k
    high_pass = [1.0, -2.0, 1.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return np.array([shelf, high_pass])


def lufs(mean_square: float) -> float:
    return max(FLOOR, OFFSET + 10 * np.log10(mean_square)) if mean_square > 0 else FLOOR


def _lufs_all(mean_squares: np.ndarray) -> np.ndarray:
    return np.maximum(FLOOR, OFFSET + 10 * np.log10(np.maximum(mean_squares, 1e-12)))


class Loudness:
    def __init__(self, rate: int):
        # Only the first two channels count (a mono source counts once): the tap delivers a stereo mix.
        self.rate = rate
        self._sos = _k_weighting(rate)
        self._zi: np.ndarray | None = None
        self._segments: deque[tuple[int, float]] = deque()    # (samples, K-weighted energy) per pushed block, newest last
        self._span = 0                                         # samples held in _segments: just over 3 s
        self._total = 0
        self._step = max(1, rate // 10)                       # a gating block every 100 ms
        self._next_block = self._step
        self._gate: list[float] = []                           # mean square of each 400 ms block
        self._short: list[float] = []                          # mean square of each 3 s block
        self._hist = np.zeros(HIST_BINS, dtype=int)
        self._integrated: float | None = None
        self._lra: float | None = None
        self._tail: np.ndarray | None = None
        self._tp = 0.0                                         # highest true peak so far, linear

    def push(self, block: np.ndarray) -> dict:
        x = block[:, :2].astype(np.float64) if block.ndim == 2 else block[:, None].astype(np.float64)
        if self._zi is None:
            self._zi = np.zeros((len(self._sos), 2, x.shape[1]))
            self._tail = np.zeros((2 * TP_PAD, x.shape[1]))
        y, self._zi = sosfilt(self._sos, x, axis=0, zi=self._zi)
        n = len(x)
        self._segments.append((n, float(np.square(y).sum())))
        self._span += n
        need = round(3 * self.rate)
        while len(self._segments) > 1 and self._span - self._segments[0][0] >= need:
            self._span -= self._segments.popleft()[0]
        self._total += n
        self._track_true_peak(x)
        if self._total >= self._next_block:
            self._close_block()
            self._next_block = (self._total // self._step + 1) * self._step
        return self._reading()

    # ---- windows ----
    def _window(self, seconds: float) -> float:
        """Mean square over the newest `seconds` of audio (what there is of it at the start)."""
        need, got, energy = round(seconds * self.rate), 0, 0.0
        for n, e in reversed(self._segments):
            if got + n >= need:
                energy += e * (need - got) / n
                got = need
                break
            got += n
            energy += e
        return energy / got if got else 0.0

    def _close_block(self) -> None:
        if self._total >= 0.4 * self.rate:
            self._gate.append(self._window(0.4))
            self._integrated = self._gated_mean_lufs(self._gate, 10.0)
        if self._total >= 3 * self.rate:
            s = self._window(3.0)
            self._short.append(s)
            level = lufs(s)
            if level > ABS_GATE:
                self._hist[int(np.clip(np.floor(level) - HIST_LO, 0, HIST_BINS - 1))] += 1
            self._lra = self._range(self._short)

    # ---- the gated figures ----
    @staticmethod
    def _gated_mean_lufs(blocks: list[float], relative_lu: float) -> float | None:
        z = np.array(blocks)
        z = z[_lufs_all(z) > ABS_GATE]
        if not len(z):
            return None
        z = z[_lufs_all(z) > lufs(float(z.mean())) - relative_lu]
        return round(lufs(float(z.mean())), 1) if len(z) else None

    @staticmethod
    def _range(blocks: list[float]) -> float | None:
        z = np.array(blocks)
        z = z[_lufs_all(z) > ABS_GATE]
        if not len(z):
            return None
        levels = _lufs_all(z)
        levels = levels[levels > lufs(float(z.mean())) - 20.0]
        if len(levels) < 2:
            return None
        return round(float(np.percentile(levels, 95) - np.percentile(levels, 10)), 1)

    def _track_true_peak(self, x: np.ndarray) -> None:
        """Peak of the 4x oversampled signal. Each sample is measured once, with TP_PAD samples of context either side
        (so it lags by TP_PAD samples, ~0.5 ms), because the filter rings wrongly where it runs out of signal."""
        buf = np.vstack([self._tail, x])
        up = resample_poly(buf, OVERSAMPLE, 1, axis=0)[TP_PAD * OVERSAMPLE:(len(buf) - TP_PAD) * OVERSAMPLE]
        if len(up):
            self._tp = max(self._tp, float(np.abs(up).max()))
        self._tail = buf[-2 * TP_PAD:]

    def _reading(self) -> dict:
        tp = round(max(FLOOR, 20 * np.log10(self._tp)), 1) if self._tp > 0 else FLOOR
        return {"momentary": round(lufs(self._window(0.4)), 1), "short": round(lufs(self._window(3.0)), 1),
                "integrated": self._integrated, "lra": self._lra, "true_peak": tp,
                "plr": round(tp - self._integrated, 1) if self._integrated is not None else None,
                "hist": self._hist.tolist()}
