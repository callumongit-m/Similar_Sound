"""Synthetic house loops with known ground truth, for evals that don't need real songs."""
import numpy as np

SR = 44100


def _kick(n=SR // 4):
    t = np.arange(n) / SR
    f = 50 + 100 * np.exp(-t * 40)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 12)


def _hat(rng, n=SR // 20):
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    x = np.diff(x, prepend=0)  # crude highpass
    return x * np.exp(-t * 90) * 0.4


def _place(buf, hit, t):
    i = int(t * SR)
    if 0 <= i < len(buf):
        j = min(len(buf), i + len(hit))
        buf[i:j] += hit[: j - i]


def drums(bpm=124, bars=8, swing=0.0, seed=0):
    rng = np.random.default_rng(seed)
    step = 60 / bpm / 4
    buf = np.zeros(int(bars * 16 * step * SR) + SR // 2)
    for b in range(bars):
        for s in range(16):
            t = (b * 16 + s) * step
            if s % 4 == 0:
                _place(buf, _kick(), t)
            if s % 2 == 1:  # off-beat 16ths carry the swing
                _place(buf, _hat(rng), t + swing * step)
    return buf[: int(bars * 16 * step * SR)]


def track(bpm=124, intro_bars=16, drop_bars=None, swing=0.0, seed=0):
    """Intro: hats only. Drop: kick + hats + sustained bass. Ground-truth drop = intro_bars.
    drop_bars defaults to intro_bars so the drop sits at ~50% of the track (the detector requires > 45%)."""
    drop_bars = intro_bars if drop_bars is None else drop_bars
    d = drums(bpm, intro_bars + drop_bars, swing, seed)
    step = 60 / bpm / 4
    n = len(d)
    t = np.arange(n) / SR
    bass = 0.5 * np.sin(2 * np.pi * 55 * t)
    drop_i = int(intro_bars * 16 * step * SR)
    bass[:drop_i] = 0
    # remove kicks from the intro so low end is truly absent
    intro = drums(bpm, intro_bars, swing, seed)
    hats_only = np.zeros(n)
    rng = np.random.default_rng(seed)
    for b in range(intro_bars + drop_bars):
        for s in range(16):
            if s % 2 == 1:
                _place(hats_only, _hat(rng), (b * 16 + s) * step + swing * step)
    mix = np.where(np.arange(n) < drop_i, hats_only, d + bass)
    return (mix / np.max(np.abs(mix))).astype(np.float32), bpm


def sectioned(bpm, sections, bass_amp=0.3, kick_amp=1.0, seed=0):
    """sections: [(bars, kick, bass, hats)] -> mono float32. For arrangement tests (breakdowns, drums
    carrying on through a build, steady loops). Returns (audio, [start_second_of_each_section])."""
    rng = np.random.default_rng(seed)
    step = 60 / bpm / 4
    bar = 16 * step
    total = sum(s[0] for s in sections)
    buf = np.zeros(int(total * bar * SR) + SR)
    t0, starts = 0.0, []
    for bars, kick, bass, hats in sections:
        starts.append(t0)
        for b in range(bars):
            for s in range(16):
                t = t0 + (b * 16 + s) * step
                if kick and s % 4 == 0:
                    _place(buf, _kick() * kick_amp, t)
                if hats and s % 2 == 1:
                    _place(buf, _hat(rng), t)
        if bass:
            i0, i1 = int(t0 * SR), int((t0 + bars * bar) * SR)
            buf[i0:i1] += bass_amp * np.sin(2 * np.pi * 55 * np.arange(i1 - i0) / SR)
        t0 += bars * bar
    buf = buf[: int(total * bar * SR)]
    return (buf / np.max(np.abs(buf))).astype(np.float32), starts
