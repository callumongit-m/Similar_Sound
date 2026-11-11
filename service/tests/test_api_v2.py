import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from similar_sound import coach, features, library, listen, llm
from similar_sound.api import create_app
from similar_sound.jobs import JobQueue
from similar_sound.store import Store
from tests.synth import drums
from tests.test_coach import doc as make_doc


class FakeKeys:
    """Stands in for the macOS Keychain (the tests must never touch the real one)."""
    def __init__(self): self.creds = None
    def get(self): return self.creds
    def set(self, creds): self.creds = creds
    def delete(self): self.creds = None


class SpyLLM:
    def __init__(self): self.calls = []
    def complete(self, system, prompt, history=()):
        self.calls.append((system + "\n\n" + prompt, prompt, history)); return "an answer"


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "v.db")
    d = make_doc(tid="ref1"); d["title"] = "Temptation"; d["groove"] = features.groove(drums(swing=0.0), 124)
    kinds = ("mix", "groove", "drums", "bass", "other")
    store.add(d, {k: np.random.default_rng(i).standard_normal(16).astype(np.float32) for i, k in enumerate(kinds)})
    d2 = make_doc(tid="ref2"); d2["title"] = "Free"; d2["groove"] = d["groove"]
    store.add(d2, {k: np.random.default_rng(50 + i).standard_normal(16).astype(np.float32) for i, k in enumerate(kinds)})
    project = make_doc(lufs=-16, tid="proj"); project["groove"] = d["groove"]; project["kind"] = "project"; project["seconds"] = 20
    pvec = {k: np.random.default_rng(99 + i).standard_normal(16).astype(np.float32) for i, k in enumerate(kinds)}
    lst = listen.Listener(store, make_tap=lambda w: None, ableton_running=lambda: True, analyse=lambda w: (project, pvec))
    keys, spy = FakeKeys(), SpyLLM()
    state = {"gem": lambda: spy}
    def connect(key, provider=None, base_url=None, model=None):
        if key.startswith("bad"):
            raise llm.LLMError("That key wasn't accepted. Add it again with the key icon.", "bad_key")
        if key.startswith("zzz") and not provider:
            raise llm.LLMError("I don't recognise that key's company. Pick it from the list.", "unknown_provider")
        return llm.Creds(provider or "openai", key, "gpt-4.1-nano")
    app = create_app(store, JobQueue(store, lambda *a, **k: None, lambda: True, peek=lambda s: None), lst,
                     client=lambda: state["gem"](), keys=keys, connect=connect, backfill=lambda st: 0)
    with TestClient(app) as c:
        yield c, store, keys, spy, state


# ---------- key ----------
def test_key_is_checked_then_saved_and_never_returned(env):
    c, store, keys, spy, state = env
    assert c.get("/settings").json() == {"configured": False, "provider": None, "name": None, "model": None}
    r = c.post("/settings/llm", json={"key": "bad" + "x" * 30})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_key" and keys.creds is None
    good = "sk-proj-" + "x" * 35
    r = c.post("/settings/llm", json={"key": good})
    assert r.status_code == 200 and good not in r.text
    assert r.json() == {"configured": True, "provider": "openai", "name": "OpenAI", "model": "gpt-4.1-nano"}
    assert keys.creds["key"] == good and good not in c.get("/settings").text
    c.delete("/settings/llm")
    assert keys.creds is None and c.get("/settings").json()["configured"] is False


def test_an_unrecognised_key_asks_which_company_and_lists_them(env):
    c, store, keys, spy, state = env
    r = c.post("/settings/llm", json={"key": "zzz" + "x" * 35})
    d = r.json()["detail"]
    assert r.status_code == 400 and d["code"] == "unknown_provider"
    assert {"gemini", "anthropic", "openai", "custom"} <= {p["id"] for p in d["providers"]}
    r = c.post("/settings/llm", json={"key": "zzz" + "x" * 35, "provider": "mistral"})
    assert r.status_code == 200 and r.json()["provider"] == "mistral"
    assert c.get("/settings/providers").status_code == 200


def test_ask_failures_carry_the_exact_sentence_and_a_code(env):
    c, store, keys, spy, state = env
    state["gem"] = lambda: (_ for _ in ()).throw(llm.NoKey())
    r = c.post("/tracks/ref1/ask", json={"question": "hi"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "no_key"
    class Broke:
        def complete(self, s, p, h=()): raise llm.OutOfCredits()
    state["gem"] = lambda: Broke()
    r = c.post("/tracks/ref1/ask", json={"question": "hi"})
    assert r.json()["detail"] == {"message": "I need more token credits", "code": "credits"}
    assert c.post("/tracks/nope/ask", json={"question": "hi"}).status_code == 404


# ---------- collections ----------
def test_collections_round_trip(env):
    c, store, keys, spy, state = env
    cid = c.post("/collections", json={"name": "UK minimal"}).json()["id"]
    assert c.post("/collections", json={"name": "UK minimal"}).status_code == 400            # duplicate
    assert c.post(f"/collections/{cid}/tracks", json={"track_id": "ref1"}).status_code == 200
    assert c.post(f"/collections/{cid}/tracks", json={"track_id": "nope"}).status_code == 400
    assert c.get("/collections").json() == [{"id": cid, "name": "UK minimal", "track_ids": ["ref1"]}]
    c.patch(f"/collections/{cid}", json={"name": "Rolling"})
    assert c.get("/collections").json()[0]["name"] == "Rolling"
    c.delete(f"/collections/{cid}/tracks/ref1")
    assert c.get("/collections").json()[0]["track_ids"] == []
    c.delete(f"/collections/{cid}")
    assert c.get("/collections").json() == [] and len(c.get("/tracks").json()) == 2          # songs survive


# ---------- project + compare ----------
def _hear(c):
    assert c.post("/project/file", json={"path": "/tmp/anything.wav"}).json()["state"] == "ready"


def test_cannot_compare_before_anything_was_heard(env):
    c, *_ = env
    r = c.post("/compare", json={"kind": "track", "id": "ref1"})
    assert r.status_code == 409 and "Play something in Ableton" in r.json()["detail"]


def test_compare_against_a_song_and_a_collection_then_ask_about_it(env):
    c, store, keys, spy, state = env
    _hear(c)
    assert c.get("/listen").json()["heard"]["bpm"] == 124
    one = c.post("/compare", json={"kind": "track", "id": "ref1"}).json()
    assert one["reference"] == "Temptation" and one["small"] is False and one["gaps"][0]["n"] == 1
    assert [a["label"] for a in one["feel"]["aspects"]] == ["Overall vibe", "Groove", "Drums", "Bass", "Synths"]
    assert set(one["why"]) == {"mix", "groove", "drums", "bass", "other"}               # the measured reasons behind each aspect
    cid = c.post("/collections", json={"name": "UK minimal"}).json()["id"]
    c.post(f"/collections/{cid}/tracks", json={"track_id": "ref1"})
    many = c.post("/compare", json={"kind": "collection", "id": cid}).json()
    assert many["is_collection"] and many["small"] is True and "rough guide" in many["note"]
    assert c.post("/compare/ask", json={"question": "what is weakest?"}).json() == {"answer": "an answer"}
    prompt, question, history = spy.calls[-1]
    assert "work-in-progress" in prompt and "UK minimal" in prompt and "what is weakest?" in prompt
    assert "HOW IT FEELS" in prompt and "closeness_0_to_100" in prompt and "typical_for_the_references" in prompt   # the LLM is told the feel scores


def test_the_feel_of_a_two_song_collection_is_measured_against_their_own_spread(env):
    c, store, keys, spy, state = env
    _hear(c)
    cid = c.post("/collections", json={"name": "Both"}).json()["id"]
    for t in ("ref1", "ref2"):
        c.post(f"/collections/{cid}/tracks", json={"track_id": t})
    feel = c.post("/compare", json={"kind": "collection", "id": cid}).json()["feel"]
    a = feel["aspects"][0]
    assert a["scope"] == "these songs" and a["pairs"] == 1 and feel["reliable"] is False and "rough guide" in feel["note"]
    assert a["closest"] and a["furthest"] and {a["closest"]["title"], a["furthest"]["title"]} <= {"Temptation", "Free"}


def test_compare_edge_cases(env):
    c, store, keys, spy, state = env
    _hear(c)
    assert c.post("/compare", json={"kind": "track", "id": "nope"}).status_code == 404
    empty = c.post("/collections", json={"name": "Empty"}).json()["id"]
    r = c.post("/compare", json={"kind": "collection", "id": empty})
    assert r.status_code == 400 and "no songs" in r.json()["detail"]
    assert c.post("/compare", json={"kind": "banana", "id": 1}).status_code == 400
    assert c.post("/compare/ask", json={"question": "x"}).status_code == 409                # nothing compared yet


def test_starting_a_new_listen_forgets_the_old_comparison(env):
    c, store, keys, spy, state = env
    _hear(c)
    c.post("/compare", json={"kind": "track", "id": "ref1"})
    c.post("/listen/cancel")
    assert c.post("/compare/ask", json={"question": "x"}).status_code == 409


def test_a_follow_up_sends_the_earlier_turns_and_only_the_recent_ones(env):
    c, store, keys, spy, state = env
    turns = [{"q": f"question {i}", "a": f"answer {i}"} for i in range(10)]
    c.post("/tracks/ref1/ask", json={"question": "and now?", "history": turns})
    prompt, question, history = spy.calls[-1]
    assert question == "and now?"
    assert len(history) == 2 * llm.MAX_HISTORY_TURNS                              # only the most recent turns travel
    assert history[0] == ("user", "question 4") and history[-1] == ("assistant", "answer 9")
    _hear(c)
    c.post("/compare", json={"kind": "track", "id": "ref1"})
    c.post("/compare/ask", json={"question": "go on", "history": turns[:2]})
    assert spy.calls[-1][2] == (("user", "question 0"), ("assistant", "answer 0"), ("user", "question 1"), ("assistant", "answer 1"))


# ---------- removing a song so it can be analysed again ----------
def test_removing_a_song_forgets_it_everywhere_and_leaves_the_others(env):
    c, store, keys, spy, state = env
    cid = c.post("/collections", json={"name": "UK minimal"}).json()["id"]
    for t in ("ref1", "ref2"):
        c.post(f"/collections/{cid}/tracks", json={"track_id": t})
    assert c.delete("/tracks/ref1").json() == {"id": "ref1"}
    assert [t["id"] for t in c.get("/tracks").json()] == ["ref2"]                       # the song is gone, the other is untouched
    assert c.get("/collections").json()[0]["track_ids"] == ["ref2"]                     # and it left the folder
    assert c.get("/tracks/ref1").status_code == 404
    assert c.get("/tracks/ref2/similar").status_code == 200
    assert store.vector("ref1", "groove") is None                                       # its vectors are gone too


def test_removing_clears_its_finished_job_records_so_no_ghost_card_is_left(env):
    c, store, keys, spy, state = env
    src = "https://soundcloud.com/x/some-song"
    from similar_sound.pipeline import track_id
    store.execute("INSERT INTO jobs(source,status,stage,created,updated) VALUES (?,?,?,0,0)", (src, "done", ""))
    store.execute("INSERT INTO jobs(source,status,stage,created,updated) VALUES (?,?,?,0,0)", (src + "?si=abc", "done", ""))
    d = make_doc(tid=track_id(src)); d["title"] = "Some Song"
    store.add(d, {"groove": np.ones(4, dtype=np.float32)})
    assert len([j for j in c.get("/status").json()["jobs"] if j["track_key"] == track_id(src)]) == 2
    c.delete(f"/tracks/{track_id(src)}")
    assert [j for j in c.get("/status").json()["jobs"] if j["track_key"] == track_id(src)] == []   # whatever share tags the URL had


def test_a_song_being_analysed_cannot_be_removed(env):
    c, store, keys, spy, state = env
    from similar_sound.pipeline import track_id
    src = "https://soundcloud.com/x/re-analysing?si=abc"
    d = make_doc(tid=track_id(src)); d["title"] = "Re-analysing"
    store.add(d, {"groove": np.ones(4, dtype=np.float32)})
    store.execute("INSERT INTO jobs(source,status,stage,created,updated) VALUES (?,?,?,0,0)", (src, "running", "stems"))
    r = c.delete(f"/tracks/{track_id(src)}")
    assert r.status_code == 409 and "being analysed right now" in r.json()["detail"]
    assert c.get(f"/tracks/{track_id(src)}").status_code == 200                              # still there


def test_removing_an_unknown_song_is_a_clear_404(env):
    c, *_ = env
    r = c.delete("/tracks/nope")
    assert r.status_code == 404 and "isn't in your library" in r.json()["detail"]


def test_after_removal_the_same_link_comes_back_unsorted_and_fresh(env):
    c, store, keys, spy, state = env
    cid = c.post("/collections", json={"name": "Keep"}).json()["id"]
    c.post(f"/collections/{cid}/tracks", json={"track_id": "ref1"})
    c.delete("/tracks/ref1")
    store.add(make_doc(tid="ref1"), {"groove": np.ones(4, dtype=np.float32)})              # what a re-analysis stores
    assert c.get("/collections").json()[0]["track_ids"] == []                                 # not back in its old folder: unsorted
