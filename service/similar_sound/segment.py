"""Find the build + drop window of a track.

House phrasing: intro, layers, full groove (drop 1), breakdown, build, MAIN DROP (drop 2).
A track has several drops and some are near-identical, so the main drop is picked by how a producer
hears it, not by loudness:
  1. candidates are returns of the low end after a breakdown, each with a measured *lead-in*
     (bars of reduced energy before it: the build-up);
  2. the main drop must sit past ~45% of the track (drop 1 and the opening section are excluded);
  3. the longest lead-in wins; lead-ins within 20% of the longest count as tied and the earliest
     of those wins (a track with five equal drops: the first one past the middle).
Every other candidate is returned in `alternatives`. `confidence` is low when it was a tie.
Callers can always override (`url#t=3:18`).

The measure above is raw energy below 150 Hz. If drums carry on through the build that stays high, so
there are fallbacks, tried in order. Every tier needs a candidate past 45%; one that only finds an early
drop 1 counts as "not found" and the next tier runs:
  1. breakdown rule on raw low end                       (the normal path)
  2. breakdown rule on sustained bass                    (kicks carry on, bass drops out for the build)
     harmonic part of the low band: kicks are percussive, bass fills the gaps between them
  3. breakdown rule on layered energy (all bands)        (kick AND bass carry on; hats/top end/mids drop
     out for the build and return at the drop: "layers come back")
  4. biggest sustained step in tier 2 or 3's curve       (no clear breakdown at all)
  5. raise DropNotFound -> the user is asked for a timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import librosa
import numpy as np

from . import config

MIN_BPM, MAX_BPM = 105.0, 145.0
MIN_DROP_BARS = 8           # a drop this close to the end of the track still counts (truncated window)
N_ALTERNATIVES = 5

POS_MIN = 0.45     # main drop sits past this fraction of the track
ON_FRAC = 0.20     # low end above this (vs the track's loud level) counts as "playing"
MIN_LEVEL = 0.5    # a drop must reach this fraction of the loud level
LEAD_FRAC = 0.5    # lead-in = bars below this fraction of the drop's own level
MIN_LEAD = 4       # shorter than this is a fill, not a breakdown
MIN_RUN = 6        # a drop must hold for at least this many bars
TIE_FRAC = 0.8     # lead-ins within 20% of the longest count as equal
LAYER_BANDS = [(20, 60), (60, 150), (150, 500), (500, 2000), (2000, 6000), (6000, 16000)]
MIN_FALLBACK_RISE = 0.35  # last-resort tier: a bass step smaller than this (vs loud level) is not a drop


class DropNotFound(Exception):
    """Every detection tier failed. The message is what the user sees; they reply with a timestamp."""

    def __init__(self) -> None:
        super().__init__("Sorry my system couldn't identify the main drop on this song, "
                         "just give me the timestamp and I'll be able to carry on :)")


@dataclass
class Segments:
    bpm: float
    build: tuple[float, float]
    drop: tuple[float, float]
    confidence: float  # 0..1; low means "probably pass an override"
    alternatives: list[float] = field(default_factory=list)  # other candidate drop start times, best first


def _coarse_bpm(env: np.ndarray, sr: int, hop: int) -> float:
    """Strongest tempogram peak inside the house range. Replaces beat_track + octave folding, which
    ping-pongs (89 <-> 178) when the detector locks onto a 2/3 polyrhythm and no octave fits."""
    if env.max() <= 0:
        raise ValueError("no tempo found (silence or non-rhythmic audio)")
    tg = librosa.feature.tempogram(onset_envelope=env, sr=sr, hop_length=hop).mean(axis=1)
    bpms = librosa.tempo_frequencies(len(tg), sr=sr, hop_length=hop)
    mask = (bpms >= MIN_BPM) & (bpms <= MAX_BPM)
    return float(bpms[mask][int(np.argmax(tg[mask]))])


def _refine_bpm(env: np.ndarray, frame_s: float, coarse: float) -> float:
    """The tempogram is quantised (steps of ~7 bpm at 130). Multi-bar autocorrelation lags give
    the fine resolution that keeps the bar grid from drifting."""
    env = env - env.mean()
    n = len(env)
    ac = np.correlate(env, env, mode="full")[n - 1:]
    lags = np.arange(len(ac))
    best, best_bpm = -np.inf, coarse
    for bpm in np.arange(coarse - 8, coarse + 8, 0.02):
        if not MIN_BPM <= bpm <= MAX_BPM:
            continue
        bar = 240.0 / bpm / frame_s  # frames per bar
        score = sum(np.interp(m * bar, lags, ac) for m in (1, 2, 4, 8) if m * bar < len(ac))
        if score > best:
            best, best_bpm = score, bpm
    return float(best_bpm)


def _bar_edges(y: np.ndarray, sr: int) -> tuple[float, np.ndarray]:
    """Return (bpm, bar start times). Beat grid is assumed steady (true for DAW-made house).
    Bar phase is only right modulo a beat; the drop is later snapped to the real downbeat."""
    hop = 512
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    bpm = _refine_bpm(env, hop / sr, _coarse_bpm(env, sr, hop))
    beat_frames = 60.0 / bpm * sr / hop
    # Phase of the beat grid: where the onset envelope is strongest, folded over one beat.
    phases = np.linspace(0, beat_frames, 48, endpoint=False)
    scores = [env[np.round(ph + np.arange(0, len(env) - beat_frames, beat_frames)).astype(int)
                  .clip(0, len(env) - 1)].sum() for ph in phases]
    first = float(phases[int(np.argmax(scores))]) * hop / sr
    bar = 4 * 60.0 / bpm
    return bpm, np.arange(first, len(y) / sr - bar, bar)


def _low_curve(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Frame-level energy below 150 Hz and its frame times."""
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=512)
    return times, S[freqs < 150].sum(axis=0)


def _sustained_low_curve(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Energy below 150 Hz of the *harmonic* (sustained) part only. Kick hits are percussive and
    are removed; bass and sub, which fill the gaps between kicks, remain. Costs a few seconds per
    track, so it is only run when the raw measure finds no breakdown."""
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    H, _ = librosa.decompose.hpss(S, margin=2.0)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=512)
    return times, (H[freqs < 150] ** 2).sum(axis=0)


def _normalise(energy: np.ndarray) -> np.ndarray:
    """Relative to the loud level (90th percentile), not the median: breakdowns drag the median down."""
    return energy / max(np.percentile(energy, 90), 1e-12)


def _layered_energy(y: np.ndarray, sr: int, edges: np.ndarray, bar: float) -> np.ndarray:
    """Per-bar energy over all bands, each band scaled by its own loud level, then averaged: a "how many
    layers are playing" curve. Catches builds that remove hats/top end/mids while kick and bass carry on."""
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=512)
    per_band = []
    for lo, hi in LAYER_BANDS:
        e = _per_bar(times, S[(freqs >= lo) & (freqs < hi)].sum(axis=0), edges, bar)
        per_band.append(e / max(np.percentile(e, 90), 1e-12))
    return _normalise(np.mean(per_band, axis=0))


MIN_DYNAMIC_RANGE = 0.25  # lull-to-loud gap (vs loud level) below which a curve has no breakdowns at all


def _adaptive_candidates(e: np.ndarray) -> list[Candidate]:
    """Breakdown rule for curves whose quiet level is not ~0 (tiers 2 and 3): "playing" = above the
    halfway point between lull and loud. A flat curve has no halfway point; without the range guard
    its noise flips bars on/off and invents breakdowns."""
    lo, hi = np.percentile(e, 10), np.percentile(e, 90)
    if hi - lo < MIN_DYNAMIC_RANGE:
        return []
    mid = float(0.5 * (lo + hi))
    return _drop_candidates(e, mid, mid)


def _per_bar(times: np.ndarray, low: np.ndarray, edges: np.ndarray, bar: float) -> np.ndarray:
    out = []
    for t in edges:
        m = (times >= t) & (times < t + bar)
        out.append(low[m].mean() if m.any() else 0.0)
    return np.array(out)


def _find_downbeat(y: np.ndarray, sr: int, near: float, beat: float, mode: str = "low") -> float:
    """Land exactly on the drop: the strongest relative low-end jump within 3 beats either side of
    the bar edge. The bar grid is only right modulo a beat, so the true downbeat can sit up to 2 beats
    from the nearest edge (real tracks rarely start on the grid); 1 beat was a bug found on real audio.
    Relative (after/before), not raw flux: bass entering silence scores far higher than the next
    ordinary kick riding on sustained bass, even when that kick's flux is bigger."""
    hop = 128
    lo, hi = max(near - 3 * beat, 0.0), near + 3 * beat
    seg = y[int(lo * sr):int(hi * sr)]
    M = np.abs(librosa.stft(seg, n_fft=1024, hop_length=hop))
    if mode == "sustained":  # drums carry on: follow the bass entry, not the kicks (kernel ~0.7 s > kick spacing)
        M, _ = librosa.decompose.hpss(M, kernel_size=(125, 17), margin=2.0)
    S = M ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=1024)
    # "layered" drops leave the low end untouched (the kick never stops): watch everything above it.
    env = S[f >= 500].sum(axis=0) if mode == "layered" else S[f < 150].sum(axis=0)
    if env.max() <= 0:
        return near
    w = max(int(0.08 * sr / hop), 2)
    eps = 1e-3 * env.max()
    ratio = np.zeros(len(env))
    for i in range(w, len(env) - w):
        ratio[i] = (env[i:i + w].mean() + eps) / (env[i - w:i].mean() + eps)
    # Drops often arrive in stages (kick returns, sub bass follows a beat or two later with a similar
    # jump). The drop is the first entry, so take the earliest frame that is nearly as strong as the best.
    # Local peaks only: the ratio ramps up over the `w` frames before an onset, so "earliest frame
    # above the bar" would land up to ~45 ms early.
    floor = 0.5 * ratio.max()
    first = next(i for i in range(w, len(env) - w)
                 if ratio[i] >= floor and ratio[i] == ratio[i - w:i + w + 1].max())
    return lo + first * hop / sr


@dataclass
class Candidate:
    bar: int
    lead_in: int   # bars of build-up before the drop
    level: float   # low-end level of the drop relative to the track's loud level
    run: int       # bars the groove then holds
    pos: float     # fraction of the track at which the drop sits


def _drop_candidates(e: np.ndarray, on_thr: float = ON_FRAC, lead_thr: float | None = None) -> list[Candidate]:
    """Returns of the low end after a breakdown. `e` = per-bar low-end energy / the track's loud level."""
    n = len(e)
    on = e >= on_thr
    for i in range(1, n - 1):  # a one-bar dropout (fill) is not a breakdown
        if not on[i] and on[i - 1] and on[i + 1]:
            on[i] = True
    out: list[Candidate] = []
    i = 0
    while i < n:
        if not (on[i] and (i == 0 or not on[i - 1])):
            i += 1
            continue
        j = i
        while j < n and on[j]:
            j += 1
        # Low end often creeps back in stages; the drop is the strongest jump near the run's start.
        hi = min(i + 8, j - 1)
        s = max(range(i, hi + 1), key=lambda k: e[k:k + 2].mean() - (e[max(k - 2, 0):k].mean() if k else 0.0))
        level = float(e[s:min(s + 8, j)].mean())
        thr, earliest, highs, k = (LEAD_FRAC * level if lead_thr is None else lead_thr), s, 0, s - 1
        while k >= 0:  # walk back over the build until two consecutive full-level bars
            if e[k] < thr:
                earliest, highs = k, 0
            else:
                highs += 1
                if highs >= 2:
                    break
            k -= 1
        out.append(Candidate(s, s - earliest, level, j - s, s / n))
        i = j
    return [c for c in out if c.lead_in >= MIN_LEAD and c.run >= MIN_RUN and c.level >= MIN_LEVEL]


def _pick_main(cands: list[Candidate]) -> tuple[Candidate, list[Candidate], float] | None:
    """(main, alternatives best-first, confidence), or None if nothing sits past POS_MIN.
    See the module docstring for the rules."""
    late = [c for c in cands if c.pos >= POS_MIN]
    if not late:
        return None
    longest = max(c.lead_in for c in late)
    tied = [c for c in late if c.lead_in >= TIE_FRAC * longest]
    main = min(tied, key=lambda c: c.bar)
    if len(tied) == 1:
        others = max((c.lead_in for c in late if c is not main), default=0)
        confidence = 0.5 + 0.5 * (longest - others) / longest
    else:
        confidence = 0.4  # equal build-ups: chosen by position alone
    rest = sorted((c for c in cands if c is not main), key=lambda c: -c.lead_in)
    return main, rest[:N_ALTERNATIVES - 1], float(confidence)


def _strongest_rise(energy: np.ndarray) -> tuple[float, int]:
    """Last-resort tier: (rise, bar) of the biggest sustained step up, past POS_MIN only."""
    w, n = 8, len(energy)
    late = [i for i in range(1, n - MIN_DROP_BARS + 1) if i / n >= POS_MIN]
    if not late:
        return 0.0, 0
    return max((energy[i:i + w].mean() - energy[max(0, i - w):i].mean(), i) for i in late)


def _choose_drop(y, sr, edges, bar, energy):
    """Run the detection tiers in order (module docstring). Returns
    (candidates best-first, downbeat times, confidence, method). Raises DropNotFound."""
    beat = bar / 4

    def finish(picked, confidence, method, mode):
        main, rest = picked[0], picked[1]
        chosen = [main] + rest
        return chosen, [_find_downbeat(y, sr, float(edges[c.bar]), beat, mode) for c in chosen], confidence, method

    if (picked := _pick_main(_drop_candidates(energy))):
        return finish(picked, picked[2], "breakdown", "low")

    s_times, s_low = _sustained_low_curve(y, sr)
    sustained = _normalise(_per_bar(s_times, s_low, edges, bar))
    if (picked := _pick_main(_adaptive_candidates(sustained))):
        return finish(picked, min(picked[2], 0.5), "sustained_bass", "sustained")

    layered = _layered_energy(y, sr, edges, bar)
    if (picked := _pick_main(_adaptive_candidates(layered))):
        return finish(picked, min(picked[2], 0.5), "layered", "layered")

    steps = [(*_strongest_rise(sustained), "sustained", sustained), (*_strongest_rise(layered), "layered", layered)]
    rise, i, mode, curve = max(steps, key=lambda t: t[0])
    if rise < MIN_FALLBACK_RISE:
        raise DropNotFound()
    main = Candidate(i, 0, float(curve[i:i + 8].mean()), len(curve) - i, i / len(curve))
    return finish((main, []), 0.2, "step_" + mode, mode)


def estimate_bpm(y: np.ndarray, sr: int) -> float:
    """Tempo of any steady 4/4 audio (raises ValueError for silence or rhythm-free audio)."""
    return _bar_edges(y, sr)[0]


def find_segments(y: np.ndarray, sr: int, drop_start_override: float | None = None,
                  trace: dict | None = None) -> Segments:
    """Pass a dict as `trace` to have every decision recorded (tempo, per-bar energy, ranked candidates,
    downbeat snap) so a wrong drop can be diagnosed instead of guessed at."""
    bpm, edges = _bar_edges(y, sr)
    bar = 4 * 60.0 / bpm
    if len(edges) < MIN_DROP_BARS + 4:
        raise ValueError("track too short for a build + drop window")
    times, low = _low_curve(y, sr)
    energy = _per_bar(times, low, edges, bar)
    energy = _normalise(energy)
    duration = len(y) / sr
    if trace is not None:
        trace.update(bpm=round(bpm, 3), bar_s=round(bar, 4), first_edge=round(float(edges[0]), 3),
                     n_bars=len(edges), duration=round(duration, 2),
                     bar_energy=[round(float(e), 2) for e in energy])

    alternatives: list[float] = []
    if drop_start_override is not None:
        drop_bar = int(np.argmin(np.abs(edges - drop_start_override)))
        confidence = 1.0
        t_drop = float(drop_start_override)
    else:
        chosen, downbeats, confidence, method = _choose_drop(y, sr, edges, bar, energy)
        drop_bar, t_drop, alternatives = chosen[0].bar, downbeats[0], downbeats[1:]
        if trace is not None:
            trace["method"] = method
            trace["candidates"] = [{"bar": c.bar, "downbeat_s": round(t, 2), "lead_in_bars": c.lead_in,
                                    "level": round(c.level, 2), "run_bars": c.run, "pos": round(c.pos, 2),
                                    "main": c is chosen[0]} for c, t in zip(chosen, downbeats)]
    if trace is not None:
        trace.update(chosen_bar=drop_bar, downbeat_s=round(t_drop, 3), confidence=round(confidence, 3))

    t_drop = float(np.clip(t_drop, 0.0, duration))
    t_build = max(t_drop - config.BUILD_BARS * bar, 0.0)  # an early drop just gets a shorter build
    t_end = min(t_drop + config.DROP_BARS * bar, duration)  # a late drop gets a truncated window
    return Segments(bpm, (t_build, t_drop), (t_drop, t_end), confidence, alternatives)
