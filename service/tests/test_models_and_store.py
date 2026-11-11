import json
import sqlite3
import threading
import time

import numpy as np
import pytest

from similar_sound import models
from similar_sound.store import Store


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.setattr(models, "FAKE", True)
    monkeypatch.setattr(models, "IN_PROCESS", False)
    yield
    models.unload()


def test_the_worker_starts_on_first_use_and_unload_ends_the_process(fake_worker):
    assert not models.loaded()
    out = models.split(np.ones((2, 8), np.float32))
    assert set(out) >= {"drums", "bass", "other"} and models.loaded()
    pid = models.worker_pid()
    assert models.unload() is True and not models.loaded()
    models.embed(np.ones((2, 8), np.float32), 44100)          # starts a fresh one on demand
    assert models.loaded() and models.worker_pid() != pid


def test_unload_does_nothing_while_a_call_is_running(fake_worker):
    models._call("pid")
    t = threading.Thread(target=models._call, args=("slow", 1.0))
    t.start()
    time.sleep(0.3)
    assert models.unload() is False and models.loaded()      # not interrupted
    t.join()
    assert models.unload() is True


def test_a_failure_inside_the_worker_is_reported_and_the_worker_survives(fake_worker):
    with pytest.raises(RuntimeError, match="fell over"):
        models._call("boom")
    assert models.loaded() and models._call("slow", 0) == "done"


def test_a_killed_worker_is_replaced_on_the_next_call(fake_worker):
    models._call("pid")
    models._worker[0].kill()
    models._worker[0].join()
    assert isinstance(models._call("pid"), int)


def test_idle_models_are_unloaded(fake_worker, monkeypatch):
    models._call("pid")
    monkeypatch.setattr(models, "IDLE_UNLOAD_S", 0)
    monkeypatch.setattr(models, "WATCH_EVERY_S", 0.05)
    t = threading.Thread(target=models._watch, daemon=True)
    t.start()
    for _ in range(40):
        if not models.loaded():
            break
        time.sleep(0.05)
    assert not models.loaded()


def test_unload_endpoint(fake_worker):
    from fastapi.testclient import TestClient
    from similar_sound.api import create_app
    app = create_app(store=Store(":memory:"), backfill=lambda s: None)
    with TestClient(app) as c:
        models._call("pid")
        assert c.post("/models/unload").json() == {"unloaded": True, "loaded": False}
        assert c.post("/models/unload").json() == {"unloaded": False, "loaded": False}


def doc(i, **kw):
    return {"id": f"t{i}", "source": "s", "title": f"T{i}", "artist": "A", "bpm": 124.0, "key": "E min",
            "key_alternatives": ["B min"], **kw}


def test_list_reads_columns_not_documents():
    s = Store(":memory:")
    s.add(doc(1), {})
    s.execute("UPDATE tracks SET doc='not json at all'")
    assert s.list() == [{"id": "t1", "title": "T1", "artist": "A", "source": "s", "bpm": 124.0, "key": "E min",
                         "key_alternatives": ["B min"]}]


def test_update_track_keeps_the_columns_in_step():
    s = Store(":memory:")
    s.add(doc(1), {})
    s.update_track("t1", key="A min", key_alternatives=[], bpm=126.0)
    assert (s.list()[0]["key"], s.list()[0]["bpm"], s.list()[0]["key_alternatives"]) == ("A min", 126.0, [])


def test_a_library_from_before_the_columns_existed_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tracks(id TEXT PRIMARY KEY, source TEXT, title TEXT, artist TEXT, created REAL, doc TEXT)")
    db.execute("INSERT INTO tracks VALUES ('x','s','Old','Me',1,?)", (json.dumps(doc(0, id="x")),))
    db.commit(); db.close()
    s = Store(path)
    got = s.list()[0]
    assert (got["id"], got["bpm"], got["key"], got["key_alternatives"]) == ("x", 124.0, "E min", ["B min"])
    Store(path)                                                # opening again is harmless


def test_only_the_newest_projects_are_kept():
    s = Store(":memory:")
    for i in range(9):
        s.execute("INSERT INTO projects(created, doc) VALUES (?, '{}')", (i,))
    s.prune_projects()
    assert [r["id"] for r in s.execute("SELECT id FROM projects ORDER BY id")] == [5, 6, 7, 8, 9]
