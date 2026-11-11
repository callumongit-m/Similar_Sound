import numpy as np

from similar_sound import coach, features
from similar_sound.store import Store
from tests.synth import drums


def doc(swing=0.0, lufs=-8.0, tid="a"):
    g = features.groove(drums(swing=swing), 124)
    band = [-20.0] * 8
    stem = {"rms_db": -20, "band_energy_db": band, "stereo_width": [0.0, 0.1, 0.3], "centroid_hz": 2000}
    return {"id": tid, "source": tid, "title": tid, "bpm": 124, "key": "A min",
            "mix": {"lufs": lufs, "crest_db": 8, "band_energy_db": band, "stereo_width": [0.0, 0.1, 0.3]},
            "stems": {"drums": stem, "bass": stem, "other": stem},
            "groove": g, "arrangement": {"rise_db": 4, "stem_rise_db": {"drums": 3, "bass": 6, "other": 1}},
            "detection_confidence": 0.8}


class EchoLLM:
    def complete(self, system, prompt):
        self.prompt = prompt
        return "ok"


def test_gaps_rank_the_real_difference_first():
    ref = doc(swing=0.25, lufs=-8)
    yours = doc(swing=0.0, lufs=-8)
    top = coach.gaps(yours, [ref], top=3)
    assert "swing" in top[0].label and top[0].delta < -0.15


def test_gaps_zero_for_identical_tracks():
    d = doc()
    assert all(g.score < 1e-9 for g in coach.gaps(d, [d]))


def test_compare_uses_nearest_and_sends_numbers_not_audio(tmp_path):
    s = Store(tmp_path / "c.db")
    for tid, sw in (("swung", 0.25), ("straight", 0.0)):
        d = doc(swing=sw, tid=tid)
        s.add(d, {"groove": np.array(d["groove"]["vector"], dtype=np.float32)})
    yours = doc(swing=0.0, tid="me")
    llm = EchoLLM()
    out = coach.compare(s, llm, yours, {"groove": np.array(yours["groove"]["vector"], dtype=np.float32)}, k=1)
    assert out["answer"] == "ok" and "straight" in llm.prompt and out["references"] == ["straight"]  # nearest groove is the straight one


def test_compare_on_empty_library_errors(tmp_path):
    import pytest
    s = Store(tmp_path / "e.db")
    v = np.ones(148, dtype=np.float32)
    with pytest.raises(ValueError):
        coach.compare(s, EchoLLM(), doc(), {"groove": v})


def test_ask_unknown_track_raises(tmp_path):
    import pytest
    with pytest.raises(KeyError):
        coach.ask(Store(tmp_path / "e.db"), EchoLLM(), "nope", "why?")
