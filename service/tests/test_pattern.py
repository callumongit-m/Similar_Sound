import numpy as np
import pytest

from similar_sound import features, pattern
from tests.synth import SR, _hat, _kick, _place, drums

BPM = 124
STEP = 60 / BPM / 4
BARS = 8


def tone(freq, dur, amp=0.4):
    t = np.arange(int(dur * SR)) / SR
    env = np.minimum(1, np.minimum(t / 0.005, (dur - t) / 0.01).clip(0))
    return amp * np.sin(2 * np.pi * freq * t) * env


def hz(name):
    return {"E1": 41.2, "G1": 49.0, "A1": 55.0, "B1": 61.74}[name]


def build(bass_loop, chord_steps=(2, 6, 10, 14), chords_differ_on_bar=None):
    """bass_loop: {step: note} per bar, repeating every bar, one 16th long each. Synth: an E minor stab on chord_steps."""
    n = int(BARS * 16 * STEP * SR)
    bass, other = np.zeros(n), np.zeros(n)
    for b in range(BARS):
        for step, note in bass_loop.items():
            _place(bass, tone(hz(note), STEP * 0.9), (b * 16 + step) * STEP)
        for s in chord_steps:
            if chords_differ_on_bar is not None and b == chords_differ_on_bar:
                continue
            chord = sum(tone(f, STEP * 1.5, 0.15) for f in (164.8, 196.0, 246.9))     # E3 G3 B3
            _place(other, chord, (b * 16 + s) * STEP)
    return {"drums": drums(BPM, BARS), "bass": bass, "other": other}


def run(stems):
    g = features.groove(stems["drums"], BPM)
    return pattern.loops(stems, g), g


@pytest.fixture(scope="module")
def simple():
    return run(build({0: "E1", 3: "E1", 6: "G1", 10: "E1", 12: "A1"}))[0]


def test_bass_line_is_read_note_by_note(simple):
    bar = simple["bass"]["loop"][0].split()
    assert bar[0] == "E1" and bar[3] == "E1" and bar[6] == "G1" and bar[10] == "E1" and bar[12] == "A1"
    assert bar[1] == "." and bar[8] == "."                       # silence between notes is reported as silence
    assert set(simple["bass"]["notes_by_share"]) == {"E1", "G1", "A1"}


def test_a_one_bar_loop_is_reported_as_one_bar(simple):
    assert simple["bass"]["repeats_every_bars"] == 1 and simple["bass"]["bars_matching"] >= 0.95
    assert simple["whole_groove_repeats_every_bars"] == 1


def test_synth_rhythm_and_pitch_classes(simple):
    bar = simple["other"]["loop"][0].split()
    assert [i for i, t in enumerate(bar) if t != "."] == [2, 6, 10, 14]
    assert all(set(bar[i].split("+")) == {"E", "G", "B"} for i in (2, 6, 10, 14))
    assert set(simple["other"]["pitch_classes_by_share"]) == {"E", "G", "B"}


def test_drums_are_the_four_on_the_floor_with_offbeat_hats(simple):
    bar = simple["drums"]["loop"][0].split()
    assert [i for i, t in enumerate(bar) if "k" in t] == [0, 4, 8, 12]
    assert [i for i, t in enumerate(bar) if "h" in t] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_placement_says_what_lands_where(simple):
    p = simple["what_lands_on_each_16th_step"]
    assert "kick" in p[0] and "bass" in p[0] and p[2] == "2: synth" and "hat" in p[1]   # (synthetic kicks and hats leak into the mid lane; real ones do too)


def test_a_two_bar_bass_loop_is_found():
    n = int(BARS * 16 * STEP * SR)
    stems = build({0: "E1", 8: "E1"})
    bass = np.zeros(n)
    for b in range(BARS):
        for step, note in ({0: "E1", 4: "E1", 8: "E1"} if b % 2 == 0 else {0: "E1", 6: "A1", 8: "B1"}).items():
            _place(bass, tone(hz(note), STEP * 0.9), (b * 16 + step) * STEP)
    stems["bass"] = bass
    got, _ = run(stems)
    assert got["bass"]["repeats_every_bars"] == 2 and len(got["bass"]["loop"]) == 2
    assert got["bass"]["loop"][1].split()[6] == "A1"


def test_one_odd_bar_lowers_the_match_without_changing_the_loop_length():
    got, _ = run(build({0: "E1", 6: "G1"}, chords_differ_on_bar=5))
    assert got["bass"]["repeats_every_bars"] == 1
    assert got["other"]["bars_matching"] < 1.0


def test_no_drums_means_no_loops():
    assert pattern.loops({"drums": np.zeros(SR * 10), "bass": np.zeros(SR * 10), "other": np.zeros(SR * 10)},
                         features.groove(np.zeros(SR * 10), BPM)) == {"empty": True}


def test_a_silent_bass_stem_is_reported_absent_not_read_as_notes():
    stems = build({0: "E1"})
    stems["bass"] = stems["bass"] * 1e-5 + 1e-6 * np.random.default_rng(0).standard_normal(len(stems["bass"]))   # leakage: about -90 dB
    got, _ = run(stems)
    assert got["bass"] == {"absent_in_the_drop": True}
    assert got["other"]["repeats_every_bars"] == 1                    # the other layers are unaffected


# ---- what the language model is shown

import json

from similar_sound import coach
from similar_sound.store import Store
from tests.test_coach import EchoLLM, doc as coach_doc


def with_loops(**kw):
    d = coach_doc(**kw)
    d["loops"] = run(build({0: "E1", 6: "G1"}))[0]
    return d


def test_the_model_is_shown_the_loops_with_how_to_read_them():
    shown = coach.labelled(with_loops())["loops"]
    assert "step 0" in shown["how_to_read"] and shown["bass"]["loop"][0].split()[0] == "E1"
    assert shown["synth_and_other"]["loop"] and shown["what_lands_on_each_16th_step"][0].startswith("0:")


def test_old_songs_say_they_have_no_loop_data_instead_of_going_blank():
    assert "not_measured_for_this_song" in coach.labelled(coach_doc())["loops"]


def test_the_standing_instructions_admit_synth_notes_are_a_sketch():
    assert "NOT a transcription" in coach.SYSTEM and "absent_in_the_drop" in coach.SYSTEM


def test_collection_digest_summarises_the_loops_across_songs():
    docs = [with_loops(tid=t) for t in "abc"] + [coach_doc(tid="old")]
    lp = json.loads(coach.collection_digest("Tech", docs))["loop_patterns"]
    assert lp["songs_with_loop_data"] == 3 and lp["of"] == 4
    assert lp["bass"]["most_used_notes_across_songs"]["E1"] == 3 and lp["bass"]["loop_length_in_bars_counts"] == {"1": 3}
    assert 0 in lp["steps_each_layer_lands_on_in_70_percent_or_more_of_songs"]["kick"]
    assert "bass" in lp["steps_each_layer_lands_on_in_70_percent_or_more_of_songs"]


def test_asking_about_a_song_sends_its_loops():
    store = Store(":memory:")
    d = with_loops(tid="s")
    store.add(d, {})
    llm = EchoLLM()
    coach.ask(store, llm, "s", "what does the bass do?")
    assert "E1" in llm.prompt and "how_to_read" in llm.prompt
