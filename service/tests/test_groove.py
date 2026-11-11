import numpy as np

from similar_sound import features
from tests.synth import drums


def test_swing_is_recovered():
    straight = features.groove(drums(swing=0.0), 124)
    swung = features.groove(drums(swing=0.25), 124)
    assert abs(straight["swing"]) < 0.06
    assert swung["swing"] > 0.15


def test_vector_moves_with_groove():
    a = np.array(features.groove(drums(swing=0.0), 124)["vector"])
    b = np.array(features.groove(drums(swing=0.25), 124)["vector"])
    cos = a @ b / (np.linalg.norm(a) * np.linalg.norm(b))
    assert cos < 0.999  # the swing must be visible in the vector


def test_pattern_recovered():
    g = features.groove(drums(swing=0.0), 124)
    assert all(h > 0.9 for h in g["kick"]["hit"][::4])
    assert all(h < 0.2 for i, h in enumerate(g["kick"]["hit"]) if i % 4)
    assert all(h > 0.8 for h in g["hat"]["hit"][1::2])


def test_bpm_drift_tolerated():
    g = features.groove(drums(bpm=124.6), 124.0)  # detector off by 0.6 bpm
    assert abs(g["swing"]) < 0.08


def test_empty_drums_degrade_gracefully():
    g = features.groove(np.zeros(44100 * 10), 124)
    assert g.get("empty") and len(g["vector"]) == features.GROOVE_DIM


def _cut_steps(x, steps, bpm=124):
    return x[int(steps * 60 / bpm / 4 * 44100):]


def test_kick_defines_step_zero_whatever_the_clip_start():
    # Real clips start at the drop's first low-end entry, often off the kick's downbeat. Step 0 must
    # be the kick's beat regardless, or every pattern comparison between tracks is rotated.
    for steps in (0, 1, 2, 3):
        g = features.groove(_cut_steps(drums(swing=0.0, bars=10), steps), 124)
        assert [i for i, h in enumerate(g["kick"]["hit"]) if h > 0.5] == [0, 4, 8, 12], steps


def test_swing_sign_survives_an_off_by_one_step_clip_start():
    # Cutting one step flips 16th parity: swing used to come out negative (a real bug on real audio).
    for steps in (0, 1, 2, 3):
        g = features.groove(_cut_steps(drums(swing=0.25, bars=10), steps), 124)
        assert g["swing"] > 0.12, (steps, g["swing"])
