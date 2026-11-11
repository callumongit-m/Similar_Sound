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
    def __init__(self): self.key = None
    def exists(self): return self.key is not None
    def get(self): return self.key
    def set(self, k):
        import re
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,100}", k.strip()): raise ValueError("That doesn't look like a Gemini API key.")
        self.key = k.strip()


class SpyLLM:
    def __init__(self): self.calls = []
    def complete(self, system, prompt):
        self.calls.append((system, prompt)); return "an answer"


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "v.db")
    d = make_doc(tid="ref1"); d["title"] = "Temptation"; d["groove"] = features.groove(drums(swing=0.0), 124)
    store.add(d, {"groove": np.array(d["groove"]["vector"], dtype=np.float32)})
    project = make_doc(lufs=-16, tid="proj"); project["groove"] = d["groove"]; project["kind"] = "project"; project["seconds"] = 20
    lst = listen.Listener(store, make_tap=lambda w: None, ableton_running=lambda: True, analyse=lambda w: project)
    keys, spy = FakeKeys(), SpyLLM()
    state = {"gem": lambda: spy}
    app = create_app(store, JobQueue(store, lambda *a, **k: None, lambda: True, peek=lambda s: None), lst,
                     gemini=lambda: state["gem"](), keys=keys,
                     check_key=lambda k: (_ for _ in ()).throw(llm.LLMError("Gemini didn't accept the key. Add it again.")) if k.startswith("bad") else None)
    with TestClient(app) as c:
        yield c, store, keys, spy, state


# ---------- key ----------
def test_key_is_checked_then_saved_and_never_returned(env):
    c, store, keys, spy, state = env
    assert c.get("/settings").json() == {"gemini_key": False}
    r = c.post("/settings/gemini-key", json={"key": "bad" + "x" * 30})
    assert r.status_code == 400 and "didn't accept the key" in r.json()["detail"] and keys.key is None
    r = c.post("/settings/gemini-key", json={"key": "not a key"})
    assert r.status_code == 400 and keys.key is None
    good = "AIza" + "x" * 35
    r = c.post("/settings/gemini-key", json={"key": good})
    assert r.status_code == 200 and good not in r.text and keys.key == good
    assert c.get("/settings").json() == {"gemini_key": True} and good not in c.get("/settings").text


def test_ask_failures_carry_the_exact_sentence_and_a_code(env):
    c, store, keys, spy, state = env
    state["gem"] = lambda: (_ for _ in ()).throw(llm.NoKey())
    r = c.post("/tracks/ref1/ask", json={"question": "hi"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "no_key"
    class Broke:
        def complete(self, s, p): raise llm.OutOfCredits()
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
    assert c.get("/collections").json() == [] and len(c.get("/tracks").json()) == 1          # song survives


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
    cid = c.post("/collections", json={"name": "UK minimal"}).json()["id"]
    c.post(f"/collections/{cid}/tracks", json={"track_id": "ref1"})
    many = c.post("/compare", json={"kind": "collection", "id": cid}).json()
    assert many["is_collection"] and many["small"] is True and "rough guide" in many["note"]
    assert c.post("/compare/ask", json={"question": "what is weakest?"}).json() == {"answer": "an answer"}
    system, prompt = spy.calls[-1]
    assert "work-in-progress" in prompt and "UK minimal" in prompt and "what is weakest?" in prompt


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
