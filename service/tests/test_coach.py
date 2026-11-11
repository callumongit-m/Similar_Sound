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


def test_compare_project_against_a_collection_reports_range_and_outside(tmp_path):
    refs = [doc(lufs=-8, tid="r1"), doc(lufs=-9, tid="r2"), doc(lufs=-10, tid="r3")]
    yours = doc(lufs=-16, tid="me")
    out = coach.compare_project(yours, refs, "UK minimal", is_collection=True)
    top = out["gaps"][0]
    assert top["label"] == "Mix · loudness (LUFS)" and top["outside"] is True and top["n"] == 3
    assert "below all 3" in top["text"] and "range -10.0 to -8.0" in top["text"]
    assert out["small"] is True and "only has 3 songs" in out["note"]       # fewer than 5: warn, still compare


def test_a_single_song_target_is_never_called_small():
    out = coach.compare_project(doc(lufs=-14), [doc(lufs=-8, tid="r")], "Temptation", is_collection=False)
    assert out["small"] is False and out["note"] == "" and out["gaps"][0]["n"] == 1


def test_five_or_more_songs_is_not_small():
    refs = [doc(tid=str(i)) for i in range(5)]
    assert coach.compare_project(doc(), refs, "X", is_collection=True)["small"] is False


def test_empty_collection_errors():
    import pytest
    with pytest.raises(ValueError, match="no songs"):
        coach.compare_project(doc(), [], "Empty", is_collection=True)


def test_asking_about_a_comparison_sends_the_gaps_and_the_labelled_project():
    spy = EchoLLM()
    refs = [doc(lufs=-8, tid="r1"), doc(lufs=-9, tid="r2")]
    yours = doc(lufs=-16)
    yours["groove"] = features.groove(drums(swing=0.0), 124)
    cmp = coach.compare_project(yours, refs, "UK minimal", True)
    coach.ask_about_comparison(spy, yours, cmp, "why is it so quiet?")
    assert "work-in-progress" in spy.prompt and "Mix · loudness (LUFS)" in spy.prompt
    assert "sub (20-60 Hz)" in spy.prompt and "why is it so quiet?" in spy.prompt


def test_ask_unknown_track_raises(tmp_path):
    import pytest
    with pytest.raises(KeyError):
        coach.ask(Store(tmp_path / "e.db"), EchoLLM(), "nope", "why?")


def test_llm_context_names_every_number():
    d = doc()
    d["groove"] = features.groove(drums(swing=0.0), 124)
    ctx = coach.labelled(d)
    assert set(ctx["stems"]) == {"drums", "bass", "other"}
    for stem in ctx["stems"].values():
        spec = stem["spectrum_dB_relative_to_its_own_total"]
        assert list(spec)[0].startswith("sub (20-60 Hz)") and list(spec)[-1].startswith("top (12-20 kHz)")
        assert len(spec) == 8
    assert ctx["groove"]["lanes"]["kick"]["16th_steps_hit_0_to_15_counting_from_the_kick_beat"] == [0, 4, 8, 12]


def _walk(x):
    if isinstance(x, dict):
        for v in x.values():
            yield v
            yield from _walk(v)


def test_what_the_llm_is_sent_contains_the_stems_and_their_labels():
    class Spy:
        def complete(self, system, prompt):
            self.system, self.prompt = system, prompt
            return "ok"
    from similar_sound.store import Store
    import tempfile, pathlib
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "c.db")
    d = doc()
    d["groove"] = features.groove(drums(swing=0.0), 124)
    s.add(d, {"groove": np.array(d["groove"]["vector"], dtype=np.float32)})
    spy = Spy()
    coach.ask(s, spy, "a", "how loud is the bass stem?")
    assert '"bass":{"loudness_dB"' in spy.prompt and "sub (20-60 Hz)" in spy.prompt
    assert "never invent a measurement" in spy.system
