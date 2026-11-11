"""Persistent single-worker queue.

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

from .pipeline import TrackRecord, track_id
from .segment import DropNotFound
from .store import Store


class JobQueue:
    def __init__(self, store: Store, ingest: Callable[..., TrackRecord],
                 ableton_running: Callable[[], bool], poll_s: float = 1.0):
        self._store, self._ingest, self._ableton, self._poll = store, ingest, ableton_running, poll_s
        self._user_paused = False
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
        return self._store.execute("SELECT max(id) AS i FROM jobs")[0]["i"]

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

    def resume(self) -> None:
        self._user_paused = False

    def state(self) -> str:
        if self._user_paused:
            return "paused_user"
        if self._ableton():
            return "paused_ableton"
        return "working" if self._working else "idle"

    def status(self) -> dict:
        jobs = [dict(r) for r in self._store.execute(
            "SELECT id,source,status,stage,error,track_id FROM jobs ORDER BY id DESC LIMIT 50")]
        return {"state": self.state(), "jobs": jobs}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # --- worker ---
    def _may_run(self) -> bool:
        return not self._user_paused and not self._ableton()

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
