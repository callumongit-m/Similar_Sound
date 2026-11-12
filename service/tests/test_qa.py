import json

import pytest

from similar_sound import coach, qa
from similar_sound.store import Store
from tests.test_pattern import with_loops


# ---------------------------------------------------------------- route
@pytest.mark.parametrize("question, expected", [
    ("what is the bass doing?", {"bass"}),
    ("how does the kick and hat swing?", {"drums", "groove"}),
    ("what notes does the bassline play?", {"bass", "loops"}),
    ("is there a vocal chop?", {"vocals"}),
    ("how loud is it and how wide?", {"mix"}),
    ("what's the lead synth doing in the drop?", {"leads", "arrangement"}),
])
def test_route_finds_what_a_question_is_about(question, expected):
    assert expected <= qa.route(question)


def test_a_question_naming_nothing_means_everything():
    assert qa.route("explain this track to me") == qa.ALL


def test_a_follow_up_inherits_the_previous_topic():
    hist = (("user", "what does the bass do?"), ("assistant", "It sits on E1."))
    assert qa.route("why is that?", hist) == frozenset({"bass"})
    assert qa.route("why is that?", hist) != qa.ALL
    assert qa.route("and the vocals?", hist) == {"vocals"}                  # naming a new topic replaces the old


# ---------------------------------------------------------------- check
@pytest.mark.parametrize("text", [
    "Based on the data, the bass is steady.", "based off what I see here", "According to the measurements, it swings.",
    "The data shows a wide hat.", "The measurements indicate a loud kick.", "From the analysis, it is minor.",
    "Based upon this the kick is strong.", "Pulled from the audio, the bass is E1.", "I cannot hear the audio, but it is E1.",
    "As an AI I can't say.", "This audio data has swing.",
])
def test_the_voice_check_catches_narrated_sources(text):
    assert any("data" in p or "narrates" in p or "hear" in p or "AI" in p for p in qa.check(text, "swing 0.1 E1"))


@pytest.mark.parametrize("text", [
    "The bass sits on E1 and F1, mostly E1.", "Kick lands on every quarter note; hats on the off-beats.",
    "The pad holds E, G and B while the lead stabs on step 2.", "That is a 2 bar loop and the swing is 0.12.",
])
def test_clean_answers_pass(text):
    assert qa.check(text, "swing 0.12 bass E1 F1 loop 2 bars") == []


def test_numbers_the_data_contains_pass_and_made_up_ones_are_caught():
    ctx = '{"bpm":127.02,"lufs":-8.4,"swing":0.12,"bass":{"loudness_dB":-10.75},"rise":3.54}'
    assert qa.unsupported_numbers("It runs at 127 BPM and -8.4 LUFS.", ctx) == []                  # rounding is fine
    assert qa.unsupported_numbers("The swing is 12 percent of a 16th.", ctx) == []                  # 0.12 as a percentage
    assert qa.unsupported_numbers("That is 2.2 dB louder than the lead's 8.4.", ctx + '{"x":6.2}') == []
    assert qa.unsupported_numbers("The bass is 14 steps long at 12 bars.", ctx) == []               # steps and bars are free
    assert qa.unsupported_numbers("The kick is at -31.5 dB and 140 BPM.", ctx) == ["-31.5", "140"]  # invented


def test_scrub_cuts_leading_clauses_and_offending_sentences():
    assert qa.scrub("Based on the data, the bass is on E1.") == "The bass is on E1."
    assert qa.scrub("The kick is steady. The data shows a wide hat. Hats are off-beat.") == "The kick is steady. Hats are off-beat."
    assert qa.scrub("According to the measurements it is loud") == "It is loud"


# ---------------------------------------------------------------- the graph
class Scripted:
    """A model that answers from a script and records every call."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, prompt, history=()):
        self.calls.append((system, prompt, history))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


SRC = qa.Source("BRIEF", lambda topics: "CTX " + ",".join(sorted(topics)) + " swing 0.12 bpm 127")


def test_a_clean_answer_costs_exactly_one_model_call():
    llm = Scripted("The swing is 0.12.")
    assert qa.answer(llm, SRC, "how does it swing?") == "The swing is 0.12."
    assert len(llm.calls) == 1
    system, prompt, history = llm.calls[0]
    assert qa.VOICE in system and "BRIEF" in system and "CTX groove" in system and prompt == "how does it swing?"


def test_a_narrating_answer_is_rewritten_once_and_the_rewrite_is_used():
    llm = Scripted("Based on the data, the swing is 0.12.", "The swing is 0.12.")
    assert qa.answer(llm, SRC, "swing?") == "The swing is 0.12."
    assert len(llm.calls) == 2
    repair_prompt = llm.calls[1][1]
    assert "narrates its source" in repair_prompt and "Based on the data" in repair_prompt and llm.calls[1][2] == ()


def test_an_invented_number_is_sent_back_for_repair():
    llm = Scripted("The swing is 0.55 and bpm 127.", "The swing is 0.12 and bpm 127.")
    assert qa.answer(llm, SRC, "swing?") == "The swing is 0.12 and bpm 127."
    assert "not in the data: 0.55" in llm.calls[1][1]


def test_a_model_that_will_not_comply_is_cut_in_code_after_one_rewrite():
    llm = Scripted("Based on the data, the swing is 0.12.")
    out = qa.answer(llm, SRC, "swing?")
    assert out == "The swing is 0.12." and len(llm.calls) == 2                 # one repair, then scrubbed, never a third call


def test_an_invented_number_that_survives_the_rewrite_is_not_silently_edited():
    llm = Scripted("The swing is 0.55.")
    assert qa.answer(llm, SRC, "swing?") == "The swing is 0.55." and len(llm.calls) == 2


def test_history_reaches_the_model_and_a_follow_up_keeps_its_topic():
    hist = (("user", "what does the bass do?"), ("assistant", "E1."))
    llm = Scripted("Because it is steady.")
    qa.answer(llm, SRC, "why?", hist)
    assert llm.calls[0][2] == hist and "CTX bass" in llm.calls[0][0]


# ---------------------------------------------------------------- retrieval: only the relevant slice reaches the model
def song_llm(question):
    store = Store(":memory:")
    d = with_loops(tid="s")
    d["stems"]["vocals"] = d["stems"]["leads"] = dict(d["stems"]["bass"])
    d["loops"]["vocals"] = {"absent_in_the_drop": True}
    store.add(d, {})
    llm = Scripted("The bass plays E1.")
    coach.ask(store, llm, "s", question)
    return llm.calls[0][0]


def test_a_bass_question_gets_the_bass_and_not_the_rest():
    system = song_llm("what notes does the bass play?")
    assert '"bass":{"repeats_every_bars"' in system and "how_to_read" in system
    assert '"full_mix"' not in system and '"vocals":{"loudness' not in system and '"leads":{"loudness' not in system


def test_a_loudness_question_gets_the_mix_and_not_the_loops():
    system = song_llm("how loud is the master?")
    assert '"full_mix"' in system and '"loops"' not in system and "how_to_read" not in system


def test_an_open_question_gets_everything():
    system = song_llm("explain this song")
    assert all(k in system for k in ('"full_mix"', '"stems"', '"groove"', '"loops"', "Closest tracks"))


def test_the_slice_is_much_smaller_than_the_whole():
    assert len(song_llm("what does the kick do?")) < 0.75 * len(song_llm("explain this song"))


def test_a_collection_question_about_bass_keeps_the_bass_loops_and_drops_unrelated_traits():
    docs = [with_loops(tid=t) for t in "abcd"]
    for d in docs:
        d["title"] = d["id"]
    llm = Scripted("E1 mostly.")
    coach.ask_collection(None, llm, "Tech", docs, "what bass notes do these use?")
    system = llm.calls[0][0].split("Data:")[1]            # the data, not the standing instructions
    assert "loop_patterns" in system and "most_used_notes_across_songs" in system
    assert "Mix · loudness" not in system


def test_topic_words_match_whole_words_only():
    assert qa.route("why is that the case?") == qa.ALL                       # "that" is not "hat"
    assert qa.route("is it equal in both channels?") == qa.ALL                # "equal" is not "eq"
    assert qa.route("the hats are bright") >= {"drums"}


def test_an_open_request_is_about_everything_even_after_a_narrow_question():
    hist = (("user", "what are the leads and pads doing?"), ("assistant", "Sparse."))
    assert qa.route("explain this track to me", hist) == qa.ALL
    assert qa.route("why is that?", hist) == {"leads", "pads"}                 # a short follow-up still leans on the last one
    assert qa.route("ok so given everything we have gone through what should I take away from this", hist) == qa.ALL   # long, names nothing


@pytest.mark.parametrize("text", ["The loop data shows no hits.", "From the measured spectrum data it is dark.", "The raw audio analysis says so."])
def test_narration_with_words_in_between_is_caught(text):
    assert qa.check(text, "")


def test_an_unrequested_not_known_caveat_is_caught_and_cut():
    text = "The bass is on E1. The specific timbres and exact melodies for these synths are not known."
    assert any("caveat" in p for p in qa.check(text, "E1"))
    assert qa.scrub(text) == "The bass is on E1."


def test_collection_digest_gives_each_song_its_bass_busyness_with_held_notes_kept_apart_from_new_notes():
    docs = [with_loops(tid=t) for t in "ab"]
    docs[0]["loops"]["bass"]["loop"] = ["E1 ~ ~ ~ . . . . . . . . . . . ."]          # one note held for four steps
    docs[0]["loops"]["bass"]["sounding_steps_per_bar"] = 4.0
    digest = json.loads(coach.collection_digest("Tech", docs))
    first = digest["songs"][0]
    assert first["bass_sounding_steps_per_bar"] == 4.0 and first["bass_notes_started_per_bar"] == 1.0
    assert "never call sounding steps 'hits'" in coach.COLLECTION_SYSTEM_NOTE
    assert "average_notes_started_per_bar" in digest["loop_patterns"]["bass"]


def test_a_collection_chat_does_not_coach():
    assert "no production advice" in coach.COLLECTION_SYSTEM_NOTE


def test_a_song_with_no_bass_in_its_drop_says_so_in_its_digest_line():
    d = with_loops(tid="a")
    d["loops"]["bass"] = {"absent_in_the_drop": True}
    assert json.loads(coach.collection_digest("T", [d, with_loops(tid="b")]))["songs"][0]["bass"] == "no bass playing in the drop"
