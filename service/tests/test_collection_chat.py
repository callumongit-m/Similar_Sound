import json

import numpy as np
import pytest

from similar_sound import coach, features, library
from similar_sound.store import Store
from tests.synth import drums
from tests.test_coach import EchoLLM, doc as make_doc


import copy

_BASE = {}


def song(i, lufs=-8.0, swing=0.0, bpm=128.0, key="A min", artist="Someone"):
    # the expensive part (a real groove analysis) is done once per swing value and copied, not redone for each song
    base = _BASE.get(swing)
    if base is None:
        base = _BASE[swing] = make_doc(lufs=-8.0, tid="base")
        base["groove"] = features.groove(drums(swing=swing), 124)
    d = copy.deepcopy(base)
    d["id"], d["title"], d["artist"], d["bpm"], d["key"] = f"s{i}", f"Song {i}", artist, bpm, key
    d["mix"]["lufs"] = lufs
    return d


def collection(n, **kw):
    return [song(i, **kw) for i in range(n)]


def test_the_digest_names_the_collection_and_summarises_all_its_songs():
    out = json.loads(coach.collection_digest("UK minimal", collection(5)))
    assert out["collection"] == "UK minimal" and out["songs_count"] == 5
    assert out["bpm"] == {"mean": 128.0, "range": [128.0, 128.0]}
    assert out["keys_of_the_songs"] == {"A min": 5}
    assert [s["title"] for s in out["songs"]] == [f"Song {i}" for i in range(5)]


def test_what_all_the_songs_share_is_listed_before_what_varies():
    docs = [song(i, lufs=-8.0 - 0.1 * (i % 2)) for i in range(8)]                    # loudness nearly identical...
    for i, d in enumerate(docs):
        d["mix"]["stereo_width"] = [0.0, 0.1 + 0.4 * (i % 2), 0.3]                    # ...but mid width swings from 0.1 to 0.5
    rows = sorted(coach._stat_rows(docs), key=lambda r: r["spread"])
    order = [r["metric"] for r in rows]
    assert order.index("Mix · loudness (LUFS)") < order.index("Mix · mids stereo width")        # steady beats swinging
    assert "Mix · mids stereo width" in [r["metric"] for r in rows[::-1][:coach.VARIABLE_TRAITS]]
    out = json.loads(coach.collection_digest("X", docs))
    assert "Mix · mids stereo width" in [r["metric"] for r in out["most_variable_traits"]]
    assert "Mix · mids stereo width" not in [r["metric"] for r in out["most_shared_traits"]]
    row = next(r for r in rows if r["metric"] == "Mix · loudness (LUFS)")
    assert row["songs"] == 8 and row["range"] == [-8.1, -8.0]


def test_the_common_drum_pattern_is_the_steps_most_songs_hit():
    out = json.loads(coach.collection_digest("X", collection(6)))
    kick = out["drum_patterns_16th_steps_0_to_15"]["kick"]
    assert kick["steps_hit_in_70_percent_or_more_of_songs"] == [0, 4, 8, 12]            # four on the floor, in every song
    hat = out["drum_patterns_16th_steps_0_to_15"]["hats"]
    assert hat["steps_hit_in_70_percent_or_more_of_songs"] == [1, 3, 5, 7, 9, 11, 13, 15]


def test_a_step_only_some_songs_hit_is_reported_as_sometimes():
    docs = collection(4)
    for d in docs[:2]:                                                                  # half the songs also hit step 2 on the kick
        d["groove"]["kick"]["hit"][2] = 0.9
    kick = json.loads(coach.collection_digest("X", docs))["drum_patterns_16th_steps_0_to_15"]["kick"]
    assert 2 in kick["steps_hit_in_30_to_70_percent_of_songs"] and 2 not in kick["steps_hit_in_70_percent_or_more_of_songs"]


def test_bands_nothing_lives_in_are_not_reported_as_a_shared_trait():
    out = json.loads(coach.collection_digest("X", collection(4)))
    assert not any(r["metric"].endswith("level (dB)") and r["mean"] < -40 for r in out["most_shared_traits"])


def test_the_digest_stays_small_however_big_the_collection_is():
    small = coach.collection_digest("X", collection(10))
    big = coach.collection_digest("X", collection(300))
    out = json.loads(big)
    assert out["songs_count"] == 300 and len(out["songs"]) == coach.MAX_SONG_LINES and out["more_songs_not_listed"] == 260
    assert len(big) < 24_000 and len(big) < len(small) * 4                               # 30x the songs, nowhere near 30x the text
    assert out["most_shared_traits"][0]["songs"] == 300                                  # but the statistics still count every song


def test_a_collection_chat_sends_the_digest_in_the_instructions_and_the_question_as_the_turn():
    spy = EchoLLM()
    coach.ask_collection(None, spy, "UK minimal", collection(5), "what makes these catchy?", (("user", "hi"), ("assistant", "hello")))
    assert "UK minimal" in spy.system and "most_shared_traits" in spy.system and "Song 3" in spy.system
    assert spy.prompt.endswith("what makes these catchy?") and spy.history == (("user", "hi"), ("assistant", "hello"))
    from similar_sound import qa
    assert qa.VOICE in spy.system                                                          # same voice as every other answer
    assert "do not know patch or sound-design settings or exact synth melodies" in spy.system       # honest about what was never measured


def test_an_empty_collection_cannot_be_chatted_with():
    with pytest.raises(ValueError, match="no songs"):
        coach.ask_collection(None, EchoLLM(), "Empty", [], "hello")


# ---------- the endpoint ----------
from tests.test_api_v2 import env  # noqa: E402,F401  (the same fake service the other API tests use)


def test_the_endpoint_chats_with_a_whole_collection_and_carries_the_conversation(env):
    c, store, keys, spy, state = env
    cid = c.post("/collections", json={"name": "UK minimal"}).json()["id"]
    for t in ("ref1", "ref2"):
        c.post(f"/collections/{cid}/tracks", json={"track_id": t})
    r = c.post(f"/collections/{cid}/ask", json={"question": "what do they share?", "history": [{"q": "hi", "a": "hello"}]})
    assert r.status_code == 200 and r.json() == {"answer": "an answer"}
    prompt, question, history = spy.calls[-1]
    assert "UK minimal" in prompt and '"songs_count":2' in prompt and "Temptation" in prompt and "Free" in prompt
    assert question == "what do they share?" and history == (("user", "hi"), ("assistant", "hello"))


def test_the_endpoint_refuses_an_empty_or_unknown_collection_clearly(env):
    c, *_ = env
    cid = c.post("/collections", json={"name": "Empty"}).json()["id"]
    r = c.post(f"/collections/{cid}/ask", json={"question": "x"})
    assert r.status_code == 400 and "no songs" in r.json()["detail"]
    assert c.post("/collections/999/ask", json={"question": "x"}).status_code == 400


def test_credits_and_missing_key_errors_reach_the_window_with_their_codes(env):
    from similar_sound import llm
    c, store, keys, spy, state = env
    cid = c.post("/collections", json={"name": "A"}).json()["id"]
    c.post(f"/collections/{cid}/tracks", json={"track_id": "ref1"})
    class Broke:
        def complete(self, s, p, h=()): raise llm.OutOfCredits()
    state["gem"] = lambda: Broke()
    assert c.post(f"/collections/{cid}/ask", json={"question": "x"}).json()["detail"] == {"message": "I need more token credits", "code": "credits"}
    state["gem"] = lambda: (_ for _ in ()).throw(llm.NoKey())
    assert c.post(f"/collections/{cid}/ask", json={"question": "x"}).json()["detail"]["code"] == "no_key"
