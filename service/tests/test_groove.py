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
