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
    queue = JobQueue(store, ingest, lambda: False, poll_s=0.02)
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
