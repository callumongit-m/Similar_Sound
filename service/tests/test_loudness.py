import numpy as np
import pyloudnorm

from similar_sound.loudness import FLOOR, HIST_LO, Loudness

RATE = 44100
HOP = RATE // 60


def sine(db, seconds, freq=1000.0, phase=0.0, channels=2):
    t = np.arange(int(seconds * RATE)) / RATE
    x = (10 ** (db / 20) * np.sin(2 * np.pi * freq * t + phase)).astype(np.float32)
    return np.stack([x] * channels, axis=1)


def feed(signal, meter=None):
    meter = meter or Loudness(RATE)
    out = None
    for i in range(0, len(signal), HOP):
        out = meter.push(signal[i:i + HOP])
    return out


def test_ebu_tech_3341_a_1khz_stereo_sine_at_minus_23_reads_minus_23():
    r = feed(sine(-23, 20))
    assert abs(r["integrated"] + 23) < 0.1 and abs(r["momentary"] + 23) < 0.1 and abs(r["short"] + 23) < 0.1


def test_a_mono_source_counts_once():
    r = feed(sine(-20, 10, channels=1))
    assert abs(r["integrated"] - (-20 - 3.01)) < 0.1


def test_integrated_matches_pyloudnorm_on_music_like_material():
    rng = np.random.default_rng(1)
    parts = [rng.normal(0, a, (RATE * 6, 2)) for a in (0.05, 0.2, 0.01, 0.1)] + [np.zeros((RATE * 3, 2))]
    sig = np.clip(np.vstack(parts), -1, 1).astype(np.float32)
    mine = feed(sig)["integrated"]
    assert abs(mine - pyloudnorm.Meter(RATE).integrated_loudness(sig.astype(np.float64))) < 0.15


def test_silence_is_gated_out_of_integrated():
    r = feed(np.vstack([sine(-20, 10), np.zeros((RATE * 10, 2), dtype=np.float32)]))
    assert abs(r["integrated"] + 20) < 0.25            # not -30 or so: the silent half is gated out (blocks straddling the end still count)
    assert r["momentary"] == FLOOR


def test_before_anything_loud_enough_there_is_no_integrated_value():
    r = feed(np.zeros((RATE * 2, 2), dtype=np.float32))
    assert r["integrated"] is None and r["lra"] is None and r["plr"] is None and r["momentary"] == FLOOR


def test_readings_appear_straight_away():
    r = Loudness(RATE).push(sine(-20, 1 / 60))
    assert r["momentary"] > -25


def test_true_peak_finds_the_peak_between_samples():
    # fs/4 with a 45 degree phase: every sample is +-0.707 but the waveform peaks at 1.0
    sig = sine(0, 2, freq=RATE / 4, phase=np.pi / 4)
    sig = sig * np.minimum(1, np.arange(len(sig)) / (0.2 * RATE))[:, None]      # fade in: an abrupt start would ring
    r = feed(sig.astype(np.float32))
    assert -0.3 < r["true_peak"] <= 0.2


def test_loudness_range_of_two_levels_ten_lu_apart():
    r = feed(np.vstack([sine(-30, 30), sine(-20, 30)]))
    assert 8 < r["lra"] < 11
    assert r["plr"] == round(r["true_peak"] - r["integrated"], 1)


def test_histogram_counts_short_term_readings_by_lu():
    r = feed(sine(-20, 10))
    assert r["hist"][-20 - HIST_LO] > 0 and sum(r["hist"][: -22 - HIST_LO]) == 0


def test_works_at_48k():
    sig = np.vstack([sine(-23, 10)] * 1)
    t = np.arange(48000 * 10) / 48000
    x = (10 ** (-23 / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    m = Loudness(48000)
    for i in range(0, len(x), 800):
        r = m.push(np.stack([x[i:i + 800]] * 2, axis=1))
    assert abs(r["integrated"] + 23) < 0.1
