import time

import numpy as np
from fastapi.testclient import TestClient

from similar_sound.api import create_app
from similar_sound.jobs import JobQueue
from similar_sound.pipeline import TrackRecord, track_id
from similar_sound.segment import DropNotFound
from similar_sound.store import Store


def ingest(source, checkpoint=lambda: None, progress=lambda s: None):
    if "#t=" not in source:
        raise DropNotFound()
    return TrackRecord({"id": track_id(source), "source": source, "title": "t"}, {"groove": np.ones(3, dtype=np.float32)})


def test_user_is_asked_for_timestamp_and_can_answer_over_http(tmp_path):
    store = Store(tmp_path / "a.db")
    queue = JobQueue(store, ingest, lambda: False, poll_s=0.02, peek=lambda s: None)
    with TestClient(create_app(store, queue)) as c:
        jid = c.post("/jobs", json={"url": "https://soundcloud.com/x/y"}).json()["id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            job = next(j for j in c.get("/status").json()["jobs"] if j["id"] == jid)
            if job["status"] == "needs_timestamp":
                break
            time.sleep(0.05)
        assert job["status"] == "needs_timestamp"
        assert "just give me the timestamp" in job["error"]
        assert c.post(f"/jobs/{jid}/timestamp", json={"timestamp": "banana"}).status_code == 400
        assert c.post("/jobs/9999/timestamp", json={"timestamp": "3:04"}).status_code == 404
        assert c.post(f"/jobs/{jid}/timestamp", json={"timestamp": "3:04"}).status_code == 200
        while time.time() < deadline:
            job = next(j for j in c.get("/status").json()["jobs"] if j["id"] == jid)
            if job["status"] == "done":
                break
            time.sleep(0.05)
        assert job["status"] == "done" and len(c.get("/tracks").json()) == 1


# ---------- Gemini: caps, errors, the credits message ----------
import io
import json
import urllib.error

from similar_sound import llm


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _ok(text="hi"):
    return lambda req, timeout=0: _Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


def _http(code, status="", message=""):
    def opener(req, timeout=0):
        raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps({"error": {"status": status, "message": message}}).encode()))
    return opener


def _gem(tmp_path, opener, key="k" * 30):
    return llm.Gemini(Store(tmp_path / "g.db"), key=lambda: key, opener=opener)


def test_out_of_credits_is_always_the_same_sentence(tmp_path):
    for code, status, msg in [(429, "RESOURCE_EXHAUSTED", "Quota exceeded"), (403, "", "billing account disabled"),
                              (429, "", ""), (400, "", "You exceeded your current quota")]:
        try:
            _gem(tmp_path, _http(code, status, msg)).complete("s", "p")
            assert False, "should raise"
        except llm.LLMError as e:
            assert str(e) == "I need more token credits", (code, status, msg, str(e))


def test_other_failures_get_their_own_plain_sentences(tmp_path):
    import pytest
    with pytest.raises(llm.LLMError, match="didn't accept the key"):
        _gem(tmp_path, _http(400, "INVALID_ARGUMENT", "API key not valid")).complete("s", "p")
    with pytest.raises(llm.LLMError, match="can't reach Gemini"):
        _gem(tmp_path, lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.URLError("down"))).complete("s", "p")
    with pytest.raises(llm.NoKey):
        _gem(tmp_path, _ok(), key=None).complete("s", "p")


def test_request_is_capped_and_uses_the_cheap_model(tmp_path):
    seen = {}
    def opener(req, timeout=0):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return _ok()(req)
    _gem(tmp_path, opener).complete("sys", "x" * 100_000)
    assert "gemini-2.5-flash-lite" in seen["url"]
    assert len(seen["body"]["contents"][0]["parts"][0]["text"]) == llm.MAX_INPUT_CHARS
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == llm.MAX_OUTPUT_TOKENS
    assert "k" * 30 not in json.dumps(seen["body"])          # the key travels in a header, never the body


def test_daily_call_limit_stops_spend(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(llm, "DAILY_CALL_LIMIT", 3)
    g = _gem(tmp_path, _ok())
    for _ in range(3):
        g.complete("s", "p")
    with pytest.raises(llm.LLMError, match="daily limit"):
        g.complete("s", "p")


def test_key_shape_is_validated_before_it_can_reach_a_command_line():
    import pytest
    from similar_sound import keychain
    for bad in ['abc"; rm -rf ~', "short", "has space " * 4, 'quote"' + "a" * 30]:
        with pytest.raises(ValueError):
            keychain.set(bad)
