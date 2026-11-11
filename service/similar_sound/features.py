"""Interpretable measurements of a stem-split clip. Everything here is human-readable:
each number maps to something a producer can act on (balance, dynamics, width, groove, arrangement).
"""
from __future__ import annotations

import librosa
import numpy as np
import pyloudnorm as pyln

SR = 44100
BAND_EDGES = [20, 60, 120, 250, 500, 2000, 6000, 12000, 20000]  # 8 bands
BAND_NAMES = ["sub", "bass", "low_mid", "mid", "upper_mid", "presence", "air", "top"]
WIDTH_EDGES = [(20, 150), (150, 2000), (2000, 20000)]
GROOVE_BANDS = {"kick": (20, 200), "mid": (200, 6000), "hat": (6000, 16000)}
HOP = 128
GROOVE_DIM = 3 * 48 + 1 + 3  # per band: 16 hit + 16 offset + 16 velocity; + swing + 3 densities
N_FFT = 2048


def _power(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    S = np.abs(librosa.stft(x, n_fft=N_FFT, hop_length=HOP)) ** 2
    return S, librosa.fft_frequencies(sr=SR, n_fft=N_FFT)


def _db(x: float) -> float:
    return float(10 * np.log10(max(x, 1e-12)))


def _band_energy_db(x_mono: np.ndarray) -> list[float]:
    S, f = _power(x_mono)
    tot = S.sum()
    return [round(_db(S[(f >= lo) & (f < hi)].sum() / tot), 2)
            for lo, hi in zip(BAND_EDGES[:-1], BAND_EDGES[1:])]


def _width_by_band(stereo: np.ndarray) -> list[float]:
    if stereo.shape[0] < 2:
        return [0.0, 0.0, 0.0]
    M, S_ = (stereo[0] + stereo[1]) / 2, (stereo[0] - stereo[1]) / 2
    Pm, f = _power(M)
    Ps, _ = _power(S_)
    out = []
    for lo, hi in WIDTH_EDGES:
        m = (f >= lo) & (f < hi)
        out.append(round(float(Ps[m].sum() / (Ps[m].sum() + Pm[m].sum() + 1e-12)), 3))
    return out


def _rms_db(x: np.ndarray) -> float:
    return round(_db(float(np.mean(x ** 2))), 2)


def _low_db(x_mono: np.ndarray) -> float:
    S, f = _power(x_mono)
    return round(_db(float(S[f < 150].sum(axis=0).mean())), 2)


def _mix_metrics(stereo: np.ndarray) -> dict:
    mono = stereo.mean(0)
    lufs = pyln.Meter(SR).integrated_loudness(stereo.T) if mono.size > SR else -70.0
    peak = float(np.max(np.abs(mono))) + 1e-12
    return {
        "lufs": round(float(lufs), 2),
        "crest_db": round(_db(peak ** 2) - _db(float(np.mean(mono ** 2))), 2),
        "band_energy_db": _band_energy_db(mono),
        "stereo_width": _width_by_band(stereo),
    }


def _stem_metrics(stereo: np.ndarray) -> dict:
    mono = stereo.mean(0)
    return {
        "rms_db": _rms_db(mono),
        "band_energy_db": _band_energy_db(mono),
        "stereo_width": _width_by_band(stereo),
        "centroid_hz": round(float(librosa.feature.spectral_centroid(y=mono, sr=SR).mean()), 1),
    }


def _onsets(mono: np.ndarray, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    # The clip starts on the downbeat, so a hit at t=0 has no preceding frame to rise from.
    # Pad with silence so flux sees it, then shift times back.
    pad = int(0.1 * SR)
    S, f = _power(np.concatenate([np.zeros(pad), mono]))
    band = np.log1p(np.sqrt(S[(f >= lo) & (f < hi)]) * 10)
    env = np.maximum(0, np.diff(band, axis=1, prepend=band[:, :1])).sum(axis=0)
    if env.max() <= 0:
        return np.array([]), np.array([])
    env = env / env.max()
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=HOP, backtrack=False)
    return librosa.frames_to_time(frames, sr=SR, hop_length=HOP) - pad / SR, env[frames]


def _fit_grid(times: np.ndarray, weights: np.ndarray, bpm: float) -> tuple[float, float]:
    """Refine (step, phase) of the 16th grid so bpm drift doesn't masquerade as groove."""
    best = (np.inf, 60 / bpm / 4, 0.0)
    for b in np.arange(bpm - 1.5, bpm + 1.5, 0.02):
        step = 60 / b / 4
        for ph in np.linspace(0, step, 24, endpoint=False):
            rel = (times - ph) / step
            d = rel - np.round(rel)
            cost = float(np.sum(weights * d ** 2))
            if cost < best[0]:
                best = (cost, step, ph)
    return best[1], best[2]


def groove(drums_mono: np.ndarray, bpm: float) -> dict:
    """Per-band 16-step pattern, micro-timing and velocity. Offsets are fractions of a 16th."""
    per = {name: _onsets(drums_mono, lo, hi) for name, (lo, hi) in GROOVE_BANDS.items()}
    all_t = np.concatenate([t for t, _ in per.values()])
    all_w = np.concatenate([w for _, w in per.values()])
    if all_t.size < 8:
        return {"empty": True, "vector": [0.0] * GROOVE_DIM}
    step, phase = _fit_grid(all_t, all_w, bpm)
    # The clip starts on the drop's downbeat, so anchor step 0 to the grid point nearest t=0.
    phase = phase % step
    if phase > step / 2:
        phase -= step
    nbars = max(len(drums_mono) / SR / (16 * step), 1e-9)

    out, vec = {"step_s": round(step, 5), "bpm_fit": round(60 / step / 4, 2)}, []
    odd, even = [], []
    for name, (t, w) in per.items():
        hit, off, vel = np.zeros(16), np.zeros(16), np.zeros(16)
        if t.size:
            rel = (t - phase) / step
            idx = np.round(rel).astype(int)
            d = rel - idx
            pos = idx % 16
            for p in range(16):
                m = pos == p
                if m.any():
                    hit[p] = min(m.sum() / nbars, 1.0)
                    off[p] = float(d[m].mean())
                    vel[p] = float(w[m].mean())
            if name != "kick":
                odd += list(d[pos % 2 == 1])
                even += list(d[pos % 2 == 0])
        out[name] = {"hit": hit.round(3).tolist(), "offset": off.round(3).tolist(),
                     "velocity": vel.round(3).tolist(), "density_per_bar": round(float(t.size / nbars), 2)}
        vec += list(hit) + list(off * 2) + list(vel)
    swing = (np.mean(odd) if odd else 0.0) - (np.mean(even) if even else 0.0)
    out["swing"] = round(float(swing), 4)
    vec += [swing * 2] + [out[n]["density_per_bar"] / 16 for n in GROOVE_BANDS]
    out["vector"] = [round(float(v), 4) for v in vec]
    return out


_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def detect_key(tonal_mono: np.ndarray) -> str:
    chroma = librosa.feature.chroma_cqt(y=librosa.resample(tonal_mono, orig_sr=SR, target_sr=22050), sr=22050).mean(1)
    best, label = -2.0, "?"
    for i in range(12):
        for prof, mode in ((_MAJOR, "maj"), (_MINOR, "min")):
            c = np.corrcoef(np.roll(prof, i), chroma)[0, 1]
            if c > best:
                best, label = c, f"{_NOTES[i]} {mode}"
    return label


def extract(mix: np.ndarray, stems: dict[str, np.ndarray], bpm: float, split_s: float) -> dict:
    """mix/stems: (ch, samples) at SR covering build then drop; `split_s` is where the drop starts."""
    cut = int(split_s * SR)
    drop = lambda a: a[:, cut:]
    build = lambda a: a[:, :cut]

    tonal = (stems["bass"] + stems["other"]).mean(0)
    arrangement = {
        "build_rms_db": _rms_db(build(mix).mean(0)),
        "drop_rms_db": _rms_db(drop(mix).mean(0)),
        "build_low_db": _low_db(build(mix).mean(0)),
        "drop_low_db": _low_db(drop(mix).mean(0)),
        "stem_rise_db": {n: round(_rms_db(drop(s).mean(0)) - _rms_db(build(s).mean(0)), 2)
                         for n, s in stems.items() if n != "vocals"},
    }
    arrangement["rise_db"] = round(arrangement["drop_rms_db"] - arrangement["build_rms_db"], 2)
    return {
        "bpm": round(bpm, 2),
        "key": detect_key(tonal),
        "mix": _mix_metrics(drop(mix)),
        "stems": {n: _stem_metrics(drop(s)) for n, s in stems.items() if n != "vocals"},
        "groove": groove(drop(stems["drums"]).mean(0), bpm),
        "arrangement": arrangement,
    }
