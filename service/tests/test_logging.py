import logging
import time

from fastapi.testclient import TestClient

from similar_sound import logsetup
from similar_sound.api import create_app
from similar_sound.jobs import JobQueue
from similar_sound.segment import DropNotFound
from similar_sound.store import Store


def read(path):
    for h in logging.getLogger("similar_sound").handlers:
        h.flush()
    return "".join(p.read_text() for p in sorted(path.parent.glob(path.name + "*")))


def wait(cond, t=5):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_log_rotates_and_never_keeps_more_than_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup, "MAX_BYTES", 2_000)
    path = tmp_path / "service.log"
    logsetup.setup(path)
    for i in range(400):
        logsetup.log.info("line %d %s", i, "x" * 40)
    files = sorted(p.name for p in tmp_path.glob("service.log*"))
    assert files == ["service.log", "service.log.1", "service.log.2"]          # oldest deleted, not kept forever
    assert all(p.stat().st_size <= 2_500 for p in tmp_path.glob("service.log*"))
    assert "line 399" in path.read_text()


def test_setup_twice_does_not_double_every_line(tmp_path):
    path = tmp_path / "service.log"
    logsetup.setup(path); logsetup.setup(path)
    logsetup.log.info("once")
    assert read(path).count("once") == 1


def test_queue_events_are_logged_with_reasons(tmp_path):
    path = tmp_path / "service.log"
    logsetup.setup(path)
    s = Store(":memory:")
    outcomes = iter(["boom", "drop", "ok"])

    def ingest(source, checkpoint, progress):
        o = next(outcomes)
        if o == "boom":
            raise ValueError("bad link")
        if o == "drop":
            raise DropNotFound()
        from similar_sound.pipeline import TrackRecord
        return TrackRecord({"id": "x", "source": source, "title": "T", "artist": "A", "bpm": 124.0, "key": "E min"}, {})

    q = JobQueue(s, ingest, lambda: False, poll_s=0.01, peek=lambda src: None)
    q.start()
    for n in "abc":
        q.enqueue(f"http://x/{n}")
    assert wait(lambda: all(j["status"] != "pending" and j["status"] != "running" for j in q.status()["jobs"]))
    q.stop()
    text = read(path)
    assert "job 1 failed: http://x/a" in text and "bad link" in text and "Traceback" in text
    assert "job 2 needs a timestamp" in text and "couldn't identify the main drop" in text
    assert "done in" in text and "A - T (124.0 bpm, E min)" in text


def test_ableton_holding_is_logged_once_not_every_second(tmp_path):
    path = tmp_path / "service.log"
    logsetup.setup(path)
    running = {"v": True}
    q = JobQueue(Store(":memory:"), lambda *a, **k: None, lambda: running["v"], poll_s=0.01, peek=lambda s: None)
    q.start()
    time.sleep(0.4)
    running["v"] = False
    assert wait(lambda: "running again" in read(path))
    q.stop()
    text = read(path)
    assert text.count("paused: Ableton is open") == 1


def test_polling_and_other_successes_leave_no_trace_but_failures_do(tmp_path):
    path = tmp_path / "service.log"
    logsetup.setup(path)
    app = create_app(store=Store(":memory:"), backfill=lambda s: None)
    with TestClient(app) as c:
        for _ in range(20):
            c.get("/status")
        c.get("/tracks/nope")
    text = read(path)
    assert "GET /status" not in text and "200" not in text
    assert "GET /tracks/nope -> 404" in text
