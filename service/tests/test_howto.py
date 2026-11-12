import json
import plistlib
import re

import pytest

from similar_sound import ableton, coach, howto, qa
from similar_sound.store import Store
from tests.test_pattern import with_loops
from tests.test_qa import Scripted

CARDS = howto._cards("12")


def ids(cards):
    return [c["id"] for c in cards]


# ---------------------------------------------------------------- the cards themselves
def test_every_card_is_complete_and_points_at_the_live_12_manual():
    assert len(CARDS) >= 30 and len({c["id"] for c in CARDS}) == len(CARDS)
    for c in CARDS:
        assert c["title"] and c["keywords"] and c["steps"], c["id"]
        assert re.match(r"https://www\.ableton\.com/en/live-manual/12/", c["source"]), c["id"]
        assert c["confidence"] in ("manual", "summary"), c["id"]


def test_cards_use_mac_shortcuts_not_windows_ones():
    text = json.dumps(CARDS)
    assert "Ctrl+Alt" not in text and "Win)" not in text


def test_what_the_manual_does_not_say_is_stated_not_filled_in():
    pool = next(c for c in CARDS if c["id"] == "groove-add-from-browser")
    assert any("does not name a 'Grooves' category" in n for n in pool["notes"])


# ---------------------------------------------------------------- is it a how-to question
@pytest.mark.parametrize("q", [
    "how do I make sure my groove is fully intact?", "How can I sidechain the bass to the kick", "where is the groove pool",
    "which button commits the groove?", "how to make the bass mono", "what's the shortcut for quantize", "Where do I set track delay",
    "step by step, how do I add reverb", "I need to set up a return track",
])
def test_how_to_questions_are_recognised(q):
    assert howto.is_howto(q)


@pytest.mark.parametrize("q", [
    "what is the bass doing?", "how loud is it and how wide is the stereo image?", "how does the swing compare?",
    "explain this track to me", "why is that?", "which songs have the busiest bass?",
])
def test_ordinary_questions_are_not(q):
    assert not howto.is_howto(q)


# ---------------------------------------------------------------- retrieval
def test_the_groove_question_from_the_brief_finds_the_groove_steps():
    got = ids(howto.retrieve("how do I make sure my groove is fully intact"))
    assert "groove-apply-to-clip" in got and "groove-commit" in got


@pytest.mark.parametrize("q, expected", [
    ("how do I make the bass mono", "device-utility"),
    ("how do I sidechain the bass to the kick", "sidechain-routing"),
    ("where do I find track delay", "track-delay"),
    ("how do I add a reverb send", "return-tracks-sends"),
    ("how do I commit the groove", "groove-commit"),
    ("how do I cut the mud with an EQ high pass", "device-eq-eight"),
    ("how do I quantize my midi notes", "midi-quantize"),
    ("how do I use capture midi", "capture-midi"),
])
def test_the_right_card_comes_back_for_common_tasks(q, expected):
    assert expected in ids(howto.retrieve(q))


def test_nothing_matching_returns_nothing_rather_than_the_least_bad_card():
    assert howto.retrieve("how do I bake a cake") == []
    assert howto.retrieve("") == []


def test_retrieval_is_limited_and_best_first():
    got = howto.retrieve("how do I apply a groove and change the groove pool timing and quantize")
    assert 1 <= len(got) <= howto.TOP_K


# ---------------------------------------------------------------- what the model sees
def test_render_numbers_the_steps_names_the_users_version_and_warns_about_summaries():
    text = howto.render(howto.retrieve("how do I commit the groove") + [next(c for c in CARDS if c["confidence"] == "summary")], "12.0.15")
    assert "The user has Live 12.0.15." in text and "1. Select the clip" in text and "2. Press the Commit button" in text
    assert "Lower confidence" in text and "not sure where that control is" in text


def test_render_with_no_cards_still_tells_the_model_not_to_guess():
    text = howto.render([], None)
    assert "No stored steps match" in text and "not sure where that control is" in text and "version is unknown" in text


def test_a_newer_live_is_told_the_menus_may_differ():
    assert "menus may differ" in howto.render(howto.retrieve("how do I commit the groove"), "13.0.1")
    assert "menus may differ" not in howto.render(howto.retrieve("how do I commit the groove"), "12.0.15")


# ---------------------------------------------------------------- the check
def test_naming_a_live_feature_that_is_not_in_the_steps_is_caught():
    cards = howto.retrieve("how do I sidechain the bass to the kick")
    assert howto.unverified_terms("Open the Compressor and use Legato in the MIDI editor.", cards) == ["Legato"]
    assert howto.unverified_terms("Use EQ Eight to cut the lows.", cards) == ["EQ Eight"]            # a real device, but not in these steps
    assert howto.unverified_terms("Unfold the Compressor and turn on the Sidechain button.", cards) == []


def test_the_installed_live_version_is_read_from_the_app_bundle(tmp_path):
    for name, v in (("Ableton Live 11 Suite.app", "11.3.2 (x)"), ("Ableton Live 12 Suite.app", "12.0.15 (2024-07-15_98b4)")):
        (tmp_path / name / "Contents").mkdir(parents=True)
        (tmp_path / name / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": v}))
    assert ableton.live_version((str(tmp_path),)) == "12.0.15"
    assert ableton.live_version((str(tmp_path / "nowhere"),)) is None


# ---------------------------------------------------------------- inside the question graph
SRC = qa.Source("BRIEF", lambda topics: "CTX swing 0.12 bpm 127")


@pytest.fixture(autouse=True)
def fixed_version(monkeypatch):
    monkeypatch.setattr(qa, "_live_version", lambda: "12.0.15")


def test_a_how_to_question_gets_the_verified_steps_and_an_ordinary_one_does_not():
    llm = Scripted("Open Clip View, then use the Clip Groove chooser.")
    qa.answer(llm, SRC, "how do I make sure my groove is fully intact?")
    system = llm.calls[0][0]
    assert "Ableton steps. The user has Live 12.0.15." in system and "Clip Groove chooser" in system and "Commit button" in system
    llm = Scripted("The swing is 0.12.")
    qa.answer(llm, SRC, "how does it swing?")
    assert "Ableton steps" not in llm.calls[0][0]


def test_an_answer_that_invents_a_menu_is_sent_back_and_the_fix_is_used():
    llm = Scripted("Open the Legato function in the MIDI editor.", "Open Clip View, then choose the groove in the Clip Groove chooser.")
    out = qa.answer(llm, SRC, "how do I apply a groove to a clip?")
    assert out.startswith("Open Clip View") and len(llm.calls) == 2
    assert "Legato" in llm.calls[1][1] and "not in the verified steps" in llm.calls[1][1]


def test_a_correct_how_to_answer_costs_one_call():
    llm = Scripted("Double-click the clip to open Clip View, then pick a groove in the Clip Groove chooser and press Commit.")
    qa.answer(llm, SRC, "how do I apply a groove to a clip and commit it?")
    assert len(llm.calls) == 1


def test_a_how_to_follow_up_uses_the_previous_question_to_find_the_steps():
    hist = (("user", "how do I make the bass mono?"), ("assistant", "Use Utility."))
    llm = Scripted("It is under Utility's Bass Mono control.")
    qa.answer(llm, SRC, "where is that?", hist)
    assert "Bass Mono Frequency slider" in llm.calls[0][0]


def test_no_matching_steps_tells_the_model_to_admit_it():
    llm = Scripted("I'm not sure where that control is.")
    qa.answer(llm, SRC, "how do I bake a cake in Ableton?")
    assert "No stored steps match" in llm.calls[0][0]


def test_route_adds_howto_only_for_how_to_questions():
    assert "howto" in qa.route("how do I make sure my groove is intact?") and "groove" in qa.route("how do I make sure my groove is intact?")
    assert "howto" not in qa.route("how does the groove feel?")
    assert qa.route("explain how to do this") == qa.ALL


def test_a_song_chat_how_to_question_sends_the_song_slice_and_the_steps():
    store = Store(":memory:")
    d = with_loops(tid="s")
    store.add(d, {})
    llm = Scripted("Press Commit under the Clip Groove chooser.")
    coach.ask(store, llm, "s", "how do I make sure the groove stays intact when I copy it into my project?")
    system = llm.calls[0][0]
    assert "Commit button" in system and '"groove"' in system


def test_the_answer_may_not_mention_the_verified_steps_or_the_manual():
    for t in ("I am not sure where that is in the verified steps.", "Per the Ableton manual, press Commit.", "The steps given do not cover it."):
        assert any("where its Ableton steps come from" in p for p in qa.check(t, ""))
    assert qa.check("Press Commit under the Clip Groove chooser.", "") == []


def test_a_single_voice_groove_card_is_not_dragged_in_by_sidechain_questions():
    assert "groove-single-voice" not in ids(howto.retrieve("how do I sidechain the bass to the kick"))
