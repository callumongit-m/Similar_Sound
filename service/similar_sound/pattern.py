"""What each layer of the drop actually plays: the loops.

features.py measures how the sound is shaped (levels, spectrum, width, groove). This module reads the notes and
rhythms: the bass line step by step, the rhythm and harmony of the synth/other layer, how many bars each layer
takes before it repeats, and which layers land on which of the 16 steps. It is data for the language model, never
shown as a picture, so it is written to be read: note names, step numbers, short patterns.

Interface: loops(stems_mono, groove_doc) -> dict. It reuses the bar grid that groove() fitted (so "step 0" means
the same thing everywhere) and returns {"empty": True} when there is no usable grid or fewer than two bars.

What it can and cannot do (the model is told this too):
- Bass is one note at a time, so its pitch per step is reliable.
- Synths and pads are chords: it reports which pitch classes sound and on which steps they hit, not a transcription.
- Drums reuse the groove lanes.
"""
from __future__ import annotations

from collections import Counter

import librosa
import numpy as np

from . import features

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
ANALYSIS_SR = 22050
MAX_LOOP_BARS = 8                      # shortest period searched: 1, 2, 4, 8 bars
MATCH_ENOUGH = 0.85                    # bars this alike count as "the same loop"
SHOWN_BARS = 4                         # bars of a long loop written out for the model
BASS_RANGE_HZ = (30.0, 300.0)
SILENT_DB = -50.0                      # a stem this quiet is leakage, not a part: report it as absent rather than read noise
CHROMA_FLOOR = 0.6                     # a pitch class counts if it has this share of the step's strongest one
LAYER_ONSET_BAND = {"bass": (20, 250), "other": (200, 8000)}
LAYER_PROMINENCE = {"bass": features.ONSET_PROMINENCE, "other": 0.5}   # a held chord ripples; only real stabs should count


def _note(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


class _Grid:
    """The 16-step bars of the drop, from the groove fit."""

    def __init__(self, groove: dict, seconds: float):
        self.step = groove["step_s"]
        self.start = groove["phase_s"] + groove["kick_shift"] * self.step     # time of bar 0, step 0
        self.shift = groove["kick_shift"]
        self.phase = groove["phase_s"]
        self.bars = int((seconds - self.start) // (16 * self.step)) if self.step > 0 else 0

    def t(self, bar: int, step: int) -> float:
        return self.start + (bar * 16 + step) * self.step

    def place(self, times: np.ndarray) -> list[tuple[int, int]]:
        """(bar, step) of each time, nearest step; hits outside the whole bars are dropped."""
        out = []
        for t in times:
            idx = int(round((t - self.phase) / self.step)) - self.shift
            if 0 <= idx < self.bars * 16:
                out.append((idx // 16, idx % 16))
        return out


def _hit_grid(grid: _Grid, mono: np.ndarray, lo: float, hi: float, prominence: float = features.ONSET_PROMINENCE) -> np.ndarray:
    times, _ = features.lane_onsets(mono, lo, hi, prominence)
    hits = np.zeros((grid.bars, 16), bool)
    for b, s in grid.place(times):
        hits[b, s] = True
    return hits


def _bass_notes(grid: _Grid, bass: np.ndarray) -> np.ndarray:
    """MIDI note sounding on each (bar, step), -1 for none. pyin tracks one pitch at a time, which suits a bassline."""
    y = librosa.resample(bass.astype(np.float32), orig_sr=features.SR, target_sr=ANALYSIS_SR)
    hop = 256
    f0, voiced, _ = librosa.pyin(y, fmin=BASS_RANGE_HZ[0], fmax=BASS_RANGE_HZ[1], sr=ANALYSIS_SR,
                                 frame_length=2048, hop_length=hop)
    times = librosa.times_like(f0, sr=ANALYSIS_SR, hop_length=hop)
    notes = np.full((grid.bars, 16), -1, int)
    for b in range(grid.bars):
        for s in range(16):
            t0 = grid.t(b, s)
            m = (times >= t0 + 0.25 * grid.step) & (times < t0 + grid.step)     # skip the attack, where pitch smears
            ok = m & voiced & np.isfinite(f0)
            if m.sum() and ok.sum() / m.sum() >= 0.5:
                notes[b, s] = int(round(69 + 12 * np.log2(np.median(f0[ok]) / 440.0)))
    return notes


def _chroma_by_step(grid: _Grid, mono: np.ndarray) -> np.ndarray:
    """(bars, 16, 12): how much of each pitch class sounds in each step, scaled so a step's strongest is 1."""
    y = librosa.resample(mono.astype(np.float32), orig_sr=features.SR, target_sr=ANALYSIS_SR)
    hop = 512
    c = librosa.feature.chroma_stft(y=y, sr=ANALYSIS_SR, n_fft=4096, hop_length=hop)
    times = librosa.times_like(c, sr=ANALYSIS_SR, hop_length=hop)
    out = np.zeros((grid.bars, 16, 12))
    for b in range(grid.bars):
        for s in range(16):
            t0 = grid.t(b, s)
            m = (times >= t0) & (times < t0 + grid.step)
            if m.any():
                v = c[:, m].mean(axis=1)
                out[b, s] = v / (v.max() + 1e-9)
    return out


def _bass_tokens(notes: np.ndarray, hits: np.ndarray) -> list[list[str]]:
    """Per step: the note if one starts there, "~" if the previous note carries on, "." if the bass is silent."""
    out = []
    for b in range(notes.shape[0]):
        row, prev = [], -1
        for s in range(16):
            n = notes[b, s]
            if n < 0:
                row.append(".")
            elif hits[b, s] or n != prev:
                row.append(_note(n))
            else:
                row.append("~")
            prev = n
        out.append(row)
    return out


def _other_tokens(hits: np.ndarray, chroma: np.ndarray) -> list[list[str]]:
    """Per step: the pitch classes sounding if a hit lands there ("E+G+B"), else ".". A rhythm and harmony sketch."""
    out = []
    for b in range(hits.shape[0]):
        row = []
        for s in range(16):
            if hits[b, s]:
                pcs = [NOTE_NAMES[i] for i in np.argsort(-chroma[b, s])[:3] if chroma[b, s, i] >= CHROMA_FLOOR]
                row.append("+".join(sorted(pcs, key=NOTE_NAMES.index)) or "x")
            else:
                row.append(".")
        out.append(row)
    return out


def _drum_tokens(groove: dict, grid: _Grid, drums: np.ndarray) -> list[list[str]]:
    lanes = {n: _hit_grid(grid, drums, lo, hi) for n, (lo, hi) in features.GROOVE_BANDS.items()}
    return [["".join(n[0] for n in lanes if lanes[n][b, s]) or "." for s in range(16)] for b in range(grid.bars)]


def _period(tokens: list[list[str]]) -> tuple[int, float]:
    """Fewest bars after which the layer repeats, and how closely it does (share of steps identical).
    The shortest period matching MATCH_ENOUGH wins, so a 4-bar loop is not reported as an 8-bar one."""
    n = len(tokens)
    best = (1, 0.0)
    for p in (1, 2, 4, 8):
        if n < 2 * p or p > MAX_LOOP_BARS:
            break
        score = float(np.mean([np.mean([a == b for a, b in zip(tokens[i], tokens[i - p])]) for i in range(p, n)]))
        if score >= MATCH_ENOUGH:
            return p, round(score, 2)
        if score > best[1]:
            best = (p, round(score, 2))
    return best


def _loop(tokens: list[list[str]], period: int) -> list[list[str]]:
    """The typical bar at each position of the loop: the commonest token at every step."""
    return [[Counter(tokens[b][s] for b in range(k, len(tokens), period)).most_common(1)[0][0] for s in range(16)]
            for k in range(period)]


def _written(bars: list[list[str]]) -> list[str]:
    return [" ".join(bar) for bar in bars[:SHOWN_BARS]]


def loops(stems: dict[str, np.ndarray], groove: dict) -> dict:
    """stems: mono drums/bass/other of the drop at features.SR. groove: the dict features.groove() returned."""
    if groove.get("empty"):
        return {"empty": True}
    grid = _Grid(groove, len(stems["drums"]) / features.SR)
    if grid.bars < 2:
        return {"empty": True}

    def silent(x):
        return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9) < SILENT_DB

    out: dict = {"bars_analysed": grid.bars}
    stems = {n: (np.zeros_like(x) if silent(x) else x) for n, x in stems.items()}
    absent = [n for n in ("bass", "other") if not stems[n].any()]
    bass_hits = _hit_grid(grid, stems["bass"], *LAYER_ONSET_BAND["bass"])
    notes = _bass_notes(grid, stems["bass"])
    bass = _bass_tokens(notes, bass_hits)
    other_hits = _hit_grid(grid, stems["other"], *LAYER_ONSET_BAND["other"], LAYER_PROMINENCE["other"])
    chroma = _chroma_by_step(grid, stems["other"])
    other = _other_tokens(other_hits, chroma)
    drums = _drum_tokens(groove, grid, stems["drums"])

    for name, tokens in (("bass", bass), ("other", other), ("drums", drums)):
        p, match = _period(tokens)
        out[name] = {"repeats_every_bars": p, "bars_matching": match, "loop": _written(_loop(tokens, p))}

    sounding = notes[notes >= 0]
    if sounding.size:
        share = Counter(_note(int(n)) for n in sounding)
        out["bass"]["notes_by_share"] = {k: round(v / sounding.size, 2) for k, v in share.most_common(5)}
        out["bass"]["sounding_steps_per_bar"] = round(float((notes >= 0).sum() / grid.bars), 1)
    pcs = Counter(tok for bar in other for t in bar if t != "." for tok in t.split("+") if tok != "x")
    if pcs:
        total = sum(pcs.values())
        out["other"]["pitch_classes_by_share"] = {k: round(v / total, 2) for k, v in pcs.most_common(6)}
    out["other"]["hits_per_bar"] = round(float(other_hits.sum() / grid.bars), 1)

    for n in absent:     # a layer that is not playing is a finding in itself ("no bass line in this drop")
        out[n] = {"absent_in_the_drop": True}

    joint = [[f"{d}|{b}|{o}" for d, b, o in zip(*bars)] for bars in zip(drums, bass, other)]
    out["whole_groove_repeats_every_bars"] = _period(joint)[0]
    # which layers land on each step of a typical bar: the "sound placement" view
    lane_name = {"k": "kick", "m": "mid-percussion", "h": "hat"}
    placement = []
    for st in range(16):
        layers = [lane_name[c] for c in "kmh" if np.mean([c in bar[st] for bar in drums]) >= 0.5]
        layers += [n for n, h in (("bass", bass_hits), ("synth", other_hits)) if h[:, st].mean() >= 0.5]
        placement.append(f"{st}: " + ("+".join(layers) if layers else "-"))
    out["what_lands_on_each_16th_step"] = placement
    return out
