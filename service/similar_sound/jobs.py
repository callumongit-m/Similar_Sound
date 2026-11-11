"""Persistent single-worker queue.

The queue holds by itself while Ableton is open (analysis is heavy and could glitch audio). The user can
press Play to run it anyway; that override lasts only until Ableton closes, so next time it holds again.
Interface: enqueue(source) is always accepted. A job whose main drop cannot be found parks as
`needs_timestamp` (with the message to show) until provide_timestamp(job_id, "3:04") resumes it. The worker runs one job at a time and holds
(between stages) while the user paused it or Ableton is open. Jobs live in SQLite, so a crash
or restart resumes them.
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

import re

from . import download
from .pipeline import TrackRecord, track_id
from .segment import DropNotFound
from .store import Store


class JobQueue:
    def __init__(self, store: Store, ingest: Callable[..., TrackRecord],
                 ableton_running: Callable[[], bool], poll_s: float = 1.0,
                 peek: Callable[[str], tuple[str, str] | None] = download.peek):
        self._store, self._ingest, self._ableton, self._poll = store, ingest, ableton_running, poll_s
        self._peek = peek
        self._user_paused = False
        self._override = False       # "run even though Ableton is open"; cleared when Ableton closes
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._working = False
        # Anything left 'running' was interrupted by a crash/restart.
        store.execute("UPDATE jobs SET status='pending', stage='' WHERE status='running'")

    # --- public ---
    def enqueue(self, source: str) -> int:
        tid = track_id(source)
        for r in self._store.execute("SELECT id,source FROM jobs WHERE status IN ('pending','running')"):
            if track_id(r["source"]) == tid:
                return r["id"]
        now = time.time()
        self._store.execute("INSERT INTO jobs(source,status,stage,created,updated) VALUES (?,?,?,?,?)",
                            (source, "pending", "", now, now))
        job_id = self._store.execute("SELECT max(id) AS i FROM jobs")[0]["i"]
        threading.Thread(target=self._name_job, args=(job_id, source), daemon=True).start()
        return job_id

    def _name_job(self, job_id: int, source: str) -> None:
        """Fetch the real song name and artist right away (cheap, not the heavy work), so the card can
        show them while the job waits or runs. Failure just leaves the name to the pipeline."""
        try:
            named = self._peek(source)
        except Exception:  # noqa: BLE001 - a missing name must never break a job
            return
        if named:
            self._set(job_id, title=named[0], artist=named[1])

    def dismiss(self, job_id: int) -> None:
        """Remove a failed / stuck job from the list (finished jobs live on as library tracks)."""
        rows = self._store.execute("SELECT status FROM jobs WHERE id=?", (job_id,))
        if not rows or rows[0]["status"] not in ("error", "needs_timestamp"):
            raise KeyError(job_id)
        self._store.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def provide_timestamp(self, job_id: int, timestamp: str) -> None:
        """Answer a `needs_timestamp` job: "3:04" or "184" re-queues it with the drop pinned there."""
        if not re.fullmatch(r"(\d+:)?\d+(\.\d+)?", timestamp.strip()):
            raise ValueError("timestamp should look like 3:04 or 184")
        rows = self._store.execute("SELECT source,status FROM jobs WHERE id=?", (job_id,))
        if not rows or rows[0]["status"] != "needs_timestamp":
            raise KeyError(job_id)
        base = rows[0]["source"].partition("#")[0]
        self._set(job_id, source=f"{base}#t={timestamp.strip()}", status="pending", error=None, stage="")

    def pause(self) -> None:
        self._user_paused = True
        self._override = False

    def resume(self) -> None:
        """Play. If Ableton is open this runs the queue anyway, until Ableton closes."""
        self._user_paused = False
        self._override = self._ableton()

    def _held_by_ableton(self) -> bool:
        running = self._ableton()
        if not running:
            self._override = False           # the override was for this Ableton session only
        return running and not self._override

    def state(self) -> str:
        if self._user_paused:
            return "paused_user"
        if self._held_by_ableton():
            return "paused_ableton"
        return "working" if self._working else "idle"

    def status(self) -> dict:
        jobs = []
        for r in self._store.execute(
                "SELECT id,source,status,stage,error,title,artist FROM jobs ORDER BY id DESC LIMIT 50"):
            # track_key: the library id this job produces, known from the start, so one card can follow
            # a track from "analysing" to "analysed" without being replaced.
            jobs.append({**dict(r), "track_key": track_id(r["source"])})
        return {"state": self.state(), "jobs": jobs}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # --- worker ---
    def _may_run(self) -> bool:
        return not self._user_paused and not self._held_by_ableton()

    def _hold(self) -> None:
        while not self._may_run() and not self._stop.is_set():
            time.sleep(self._poll)

    def _set(self, job_id: int, **cols) -> None:
        cols["updated"] = time.time()
        sets = ",".join(f"{k}=?" for k in cols)
        self._store.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*cols.values(), job_id))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._hold()
            rows = self._store.execute("SELECT id,source FROM jobs WHERE status='pending' ORDER BY id LIMIT 1")
            if not rows:
                time.sleep(self._poll)
                continue
            job_id, source = rows[0]["id"], rows[0]["source"]
            self._set(job_id, status="running")
            self._working = True
            try:
                rec = self._ingest(source, checkpoint=self._hold,
                                   progress=lambda s, j=job_id: self._set(j, stage=s))
                self._store.add(rec.doc, rec.vectors)
                self._set(job_id, status="done", stage="", track_id=rec.doc["id"])
            except DropNotFound as e:  # not a failure: the user can unblock it with one timestamp
                self._set(job_id, status="needs_timestamp", error=str(e), stage="")
            except Exception as e:  # one bad URL must not stop the queue
                traceback.print_exc()
                self._set(job_id, status="error", error=str(e)[:300])
            finally:
                self._working = False
