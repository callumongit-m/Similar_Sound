import time

import numpy as np

from similar_sound.jobs import JobQueue
from similar_sound.pipeline import TrackRecord, track_id
from similar_sound.store import Store


def fake_ingest(source, checkpoint=lambda: None, progress=lambda s: None):
    progress("stems")
    checkpoint()
    if "bad" in source:
        raise RuntimeError("boom")
    doc = {"id": track_id(source), "source": source, "title": source}
    return TrackRecord(doc, {"groove": np.ones(4, dtype=np.float32)})


def wait(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def make(tmp_path, ableton):
    s = Store(tmp_path / "t.db")
    q = JobQueue(s, fake_ingest, lambda: ableton["on"], poll_s=0.02, peek=lambda s: None)
    return s, q


def done(q, n):
    return sum(j["status"] == "done" for j in q.status()["jobs"]) >= n


def test_runs_jobs(tmp_path):
    s, q = make(tmp_path, {"on": False})
    q.start()
    q.enqueue("a"); q.enqueue("b")
    assert wait(lambda: done(q, 2))
    assert len(s.list()) == 2
    q.stop()


def test_ableton_pauses_work_but_accepts_input(tmp_path):
    ab = {"on": True}
    s, q = make(tmp_path, ab)
    q.start()
    jid = q.enqueue("a")  # accepted while Ableton is open
    assert jid
    time.sleep(0.3)
    assert q.state() == "paused_ableton"
    assert not any(j["status"] == "done" for j in q.status()["jobs"])
    ab["on"] = False
    assert wait(lambda: done(q, 1))
    q.stop()


def test_user_pause_and_resume(tmp_path):
    s, q = make(tmp_path, {"on": False})
    q.pause(); q.start()
    q.enqueue("a")
    time.sleep(0.3)
    assert q.state() == "paused_user" and not done(q, 1)
    q.resume()
    assert wait(lambda: done(q, 1))
    q.stop()


def test_bad_job_does_not_block_queue(tmp_path):
    s, q = make(tmp_path, {"on": False})
    q.start()
    q.enqueue("bad"); q.enqueue("good")
    assert wait(lambda: done(q, 1))
    statuses = {j["source"]: j["status"] for j in q.status()["jobs"]}
    assert statuses["bad"] == "error" and statuses["good"] == "done"
    q.stop()


def test_duplicate_url_deduped(tmp_path):
    s, q = make(tmp_path, {"on": True})
    assert q.enqueue("a#t=1:00") == q.enqueue("a")


def test_crash_recovery_requeues_running(tmp_path):
    s = Store(tmp_path / "t.db")
    s.execute("INSERT INTO jobs(source,status,stage,created,updated) VALUES ('a','running','stems',0,0)")
    q = JobQueue(s, fake_ingest, lambda: False, poll_s=0.02, peek=lambda s: None)
    q.start()
    assert wait(lambda: done(q, 1))
    q.stop()


def test_nearest_ranks_by_cosine(tmp_path):
    s = Store(tmp_path / "t.db")
    for name, v in [("x", [1, 0]), ("y", [0.9, 0.1]), ("z", [0, 1])]:
        s.add({"id": name, "source": name}, {"groove": np.array(v, dtype=np.float32)})
    res = s.nearest(np.array([1, 0]), "groove", k=3, exclude="x")
    assert [r[0] for r in res] == ["y", "z"]


def test_drop_not_found_parks_job_with_message_then_resumes_on_timestamp(tmp_path):
    from similar_sound.segment import DropNotFound
    calls = []

    def ingest(source, checkpoint=lambda: None, progress=lambda s: None):
        calls.append(source)
        if "#t=" not in source:
            raise DropNotFound()
        return fake_ingest(source, checkpoint, progress)

    s = Store(tmp_path / "t.db")
    q = JobQueue(s, ingest, lambda: False, poll_s=0.02, peek=lambda s: None)
    q.start()
    jid = q.enqueue("https://soundcloud.com/a/b")
    assert wait(lambda: any(j["status"] == "needs_timestamp" for j in q.status()["jobs"]))
    job = next(j for j in q.status()["jobs"] if j["id"] == jid)
    assert job["error"] == ("Sorry my system couldn't identify the main drop on this song, "
                            "just give me the timestamp and I'll be able to carry on :)")
    q.provide_timestamp(jid, "3:04")
    assert wait(lambda: done(q, 1))
    assert calls[-1] == "https://soundcloud.com/a/b#t=3:04"
    assert len(q.status()["jobs"]) == 1  # same job, not a new one
    q.stop()


def test_timestamp_validation_and_wrong_job(tmp_path):
    import pytest
    s, q = make(tmp_path, {"on": True})
    jid = q.enqueue("a")
    with pytest.raises(KeyError):          # job is pending, not waiting for a timestamp
        q.provide_timestamp(jid, "3:04")
    s.execute("UPDATE jobs SET status='needs_timestamp' WHERE id=?", (jid,))
    with pytest.raises(ValueError):
        q.provide_timestamp(jid, "banana")
    q.provide_timestamp(jid, "184")
    assert q.status()["jobs"][0]["source"] == "a#t=184"


def test_the_artist_the_uploader_filled_in_beats_the_upload_account():
    from similar_sound.download import split_title
    # the real case: account "Slacker 85" uploaded a track whose artist field says TB-203
    assert split_title("Another Banger", "Slacker 85", "TB-203") == ("Another Banger", "TB-203")
    assert split_title("TB-203 - Another Banger", "Slacker 85", "TB-203") == ("Another Banger", "TB-203")   # not said twice
    assert split_title("tb-203 - Another Banger", "x", "TB-203") == ("Another Banger", "TB-203")           # whatever the capitals
    assert split_title("A - B", "x", "") == ("B", "A")                                                     # no artist field: the title still works
    assert split_title("Song", "Account", "") == ("Song", "Account")                                       # and the account is the last resort
    assert split_title("Song", "Account", "   ") == ("Song", "Account")


def test_split_title_prefers_artist_dash_song_over_uploader():
    from similar_sound.download import split_title
    assert split_title("MAX DEAN - TALKING WITH MYSELF (UNRELEASED)", "J8") == ("TALKING WITH MYSELF (UNRELEASED)", "MAX DEAN")
    assert split_title("Temptation", "obp") == ("Temptation", "obp")
    assert split_title("A - B - C", "x") == ("B - C", "A")  # only the first dash splits


def test_job_is_named_at_enqueue_not_after_analysis(tmp_path):
    s = Store(tmp_path / "t.db")
    q = JobQueue(s, fake_ingest, lambda: True, poll_s=0.02, peek=lambda src: ("Temptation", "obp"))  # worker held: Ableton open
    q.enqueue("https://soundcloud.com/obpofficial/temptation-5?si=abc")
    assert wait(lambda: q.status()["jobs"][0]["title"] == "Temptation")
    job = q.status()["jobs"][0]
    assert job["artist"] == "obp" and job["status"] == "pending"   # named while still waiting


def test_a_failing_peek_never_breaks_the_job(tmp_path):
    def boom(src):
        raise RuntimeError("network down")
    s = Store(tmp_path / "t.db")
    q = JobQueue(s, fake_ingest, lambda: False, poll_s=0.02, peek=boom)
    q.start(); q.enqueue("a")
    assert wait(lambda: done(q, 1)); q.stop()


def test_status_carries_the_track_key_from_the_start(tmp_path):
    s, q = make(tmp_path, {"on": True})
    q.enqueue("https://soundcloud.com/x/y?si=1")
    assert q.status()["jobs"][0]["track_key"] == track_id("https://soundcloud.com/x/y")  # tracking params ignored


def test_dismiss_only_failed_or_stuck_jobs(tmp_path):
    import pytest
    s, q = make(tmp_path, {"on": True})
    jid = q.enqueue("a")
    with pytest.raises(KeyError):
        q.dismiss(jid)                     # pending work cannot be dismissed
    s.execute("UPDATE jobs SET status='error' WHERE id=?", (jid,))
    q.dismiss(jid)
    assert q.status()["jobs"] == []


def test_same_soundcloud_song_shared_differently_is_one_track():
    a = "https://soundcloud.com/obpofficial/temptation-5?si=b60c&utm_source=clipboard&utm_medium=text"
    b = "https://soundcloud.com/obpofficial/temptation-5"
    c = "https://soundcloud.com/obpofficial/temptation-5/#t=2:30"
    assert track_id(a) == track_id(b) == track_id(c)
    assert track_id("https://soundcloud.com/obpofficial/temptation-6") != track_id(b)
    # other sites keep their query: youtube's ?v= IS the identity
    assert track_id("https://youtube.com/watch?v=aaa") != track_id("https://youtube.com/watch?v=bbb")


# ---------- Play overrides Ableton's automatic hold, for that Ableton session only ----------
def test_play_runs_the_queue_even_though_ableton_is_open(tmp_path):
    ab = {"on": True}
    s, q = make(tmp_path, ab)
    q.start(); q.enqueue("a")
    time.sleep(0.3)
    assert q.state() == "paused_ableton" and not done(q, 1)
    q.resume()                                   # the user presses Play
    assert wait(lambda: done(q, 1)) and ab["on"]    # it ran with Ableton still open
    q.stop()


def test_the_override_ends_when_ableton_closes(tmp_path):
    ab = {"on": True}
    s, q = make(tmp_path, ab)
    q.resume()
    assert q.state() == "idle"                   # overriding: not held
    ab["on"] = False
    assert q.state() == "idle"                   # Ableton closed: the override has served its purpose
    ab["on"] = True
    assert q.state() == "paused_ableton"         # next time Ableton opens it holds again


def test_pause_wins_and_cancels_the_override(tmp_path):
    ab = {"on": True}
    s, q = make(tmp_path, ab)
    q.resume(); q.pause()
    assert q.state() == "paused_user"
    q.resume()                                   # Play again with Ableton still open
    assert q.state() != "paused_user" and q.state() != "paused_ableton"


def test_resume_with_ableton_closed_is_just_resume(tmp_path):
    ab = {"on": False}
    s, q = make(tmp_path, ab)
    q.pause(); assert q.state() == "paused_user"
    q.resume(); assert q.state() == "idle"
    ab["on"] = True
    assert q.state() == "paused_ableton"         # no lingering override from a Play pressed when Ableton was closed


def test_a_job_held_mid_way_continues_when_you_press_play(tmp_path):
    ab = {"on": False}
    gate = {"held": False}
    stages = []
    def ingest(source, checkpoint=lambda: None, progress=lambda s: None):
        progress("stems"); stages.append("stems")
        ab["on"] = True                          # Ableton opens in the middle of the job
        checkpoint()                             # ...so the job holds here
        stages.append("after-checkpoint")
        return fake_ingest(source, checkpoint, progress)
    s = Store(tmp_path / "t.db")
    q = JobQueue(s, ingest, lambda: ab["on"], poll_s=0.02, peek=lambda s: None)
    q.start(); q.enqueue("a")
    assert wait(lambda: stages == ["stems"])
    time.sleep(0.2)
    assert stages == ["stems"] and q.state() == "paused_ableton"
    q.resume()
    assert wait(lambda: done(q, 1)) and stages == ["stems", "after-checkpoint"]
    q.stop()
