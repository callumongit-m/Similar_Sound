"""Key detection is ambiguous by nature: E minor and B minor (a fifth apart) share notes, and a bass that plays
the root and the fifth fits both. These pin down how that ambiguity is reported."""
import numpy as np

from similar_sound import features as F


def profile(root, minor=True, blend=None):
    prof = F._MINOR if minor else F._MAJOR
    c = np.roll(prof, root)
    if blend is not None:                         # a mix of two keys' pitch content
        other_root, w = blend
        c = (1 - w) * c + w * np.roll(prof, other_root)
    return c


E, B = F._NOTES.index("E"), F._NOTES.index("B")


def test_a_clear_key_is_reported_alone():
    info = F.key_info(F.rank_chroma(profile(E)))
    assert info == {"key": "E min", "key_alternatives": []}


def test_every_key_is_recognised_from_its_own_profile():
    for root in range(12):
        for minor in (True, False):
            want = f"{F._NOTES[root]} {'min' if minor else 'maj'}"
            assert F.key_info(F.rank_chroma(profile(root, minor)))["key"] == want


def test_e_minor_versus_b_minor_is_reported_as_both_not_guessed():
    # the user's real case: a project in E minor that was reported as B minor
    ranked = F.rank_chroma(profile(B, blend=(E, 0.5)))          # equal parts E-minor and B-minor pitch content
    info = F.key_info(ranked)
    assert {info["key"], *info["key_alternatives"]} == {"E min", "B min"}
    assert len(info["key_alternatives"]) == 1


def test_leaning_clearly_one_way_drops_the_alternative_again():
    assert F.key_info(F.rank_chroma(profile(E, blend=(B, 0.15))))["key_alternatives"] == []


def test_the_threshold_is_what_the_data_chose():
    assert F.KEY_CLOSE_CALL == 0.05


def test_extract_reports_key_and_alternatives(tmp_path):
    sr = F.SR
    t = np.arange(sr * 4) / sr
    tone = lambda f: 0.2 * np.sin(2 * np.pi * f * t)
    stems = {"drums": np.zeros((2, len(t)), np.float32), "bass": np.stack([tone(82.4)] * 2).astype(np.float32),
             "other": np.stack([tone(329.6)] * 2).astype(np.float32)}
    mix = stems["bass"] + stems["other"]
    doc = F.extract(mix, stems, 124.0, None)
    assert "key" in doc and isinstance(doc["key_alternatives"], list)
