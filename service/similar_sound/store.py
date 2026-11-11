"""SQLite library: one JSON document and a handful of vectors per track. Audio is never stored.
Vector search is brute-force cosine, which is instant at library sizes of a few thousand.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks(id TEXT PRIMARY KEY, source TEXT, title TEXT, artist TEXT,
                                  created REAL, doc TEXT);
CREATE TABLE IF NOT EXISTS vectors(track_id TEXT, kind TEXT, dim INTEGER, data BLOB,
                                   PRIMARY KEY(track_id, kind));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, status TEXT,
                                stage TEXT, error TEXT, track_id TEXT, created REAL, updated REAL,
                                title TEXT, artist TEXT);
CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, calls INTEGER);
CREATE TABLE IF NOT EXISTS collections(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, created REAL);
CREATE TABLE IF NOT EXISTS collection_tracks(collection_id INTEGER, track_id TEXT, added REAL,
                                             PRIMARY KEY(collection_id, track_id));
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, doc TEXT);
"""


class Store:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            cols = {r["name"] for r in self._db.execute("PRAGMA table_info(jobs)")}
            for col in ("title", "artist"):  # libraries created before jobs carried a name
                if col not in cols:
                    self._db.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")

    def execute(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.fetchall()

    # tracks
    def add(self, doc: dict, vectors: dict[str, np.ndarray]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?,?)",
                             (doc["id"], doc["source"], doc.get("title", ""), doc.get("artist", ""),
                              time.time(), json.dumps(doc)))
            self._db.execute("DELETE FROM vectors WHERE track_id=?", (doc["id"],))
            for kind, v in vectors.items():
                v = np.asarray(v, dtype=np.float32)
                self._db.execute("INSERT INTO vectors VALUES (?,?,?,?)", (doc["id"], kind, v.size, v.tobytes()))
            self._db.commit()

    def update_track(self, track_id: str, **fields) -> bool:
        """Change fields of a stored song in place (its document, and the title/artist columns the list reads)."""
        with self._lock:
            doc = self.get(track_id)
            if doc is None:
                return False
            doc.update(fields)
            self._db.execute("UPDATE tracks SET doc=?, title=?, artist=? WHERE id=?",
                             (json.dumps(doc), doc.get("title", ""), doc.get("artist", ""), track_id))
            self._db.commit()
        return True

    def delete_track(self, track_id: str) -> bool:
        """Forget a song entirely: its analysis, its vectors and its place in every collection. (No audio is
        ever kept, so there is nothing else to delete.) Returns whether it existed."""
        with self._lock:
            existed = self._db.execute("DELETE FROM tracks WHERE id=?", (track_id,)).rowcount > 0
            self._db.execute("DELETE FROM vectors WHERE track_id=?", (track_id,))
            self._db.execute("DELETE FROM collection_tracks WHERE track_id=?", (track_id,))
            self._db.commit()
        return existed

    def get(self, track_id: str) -> dict | None:
        rows = self.execute("SELECT doc FROM tracks WHERE id=?", (track_id,))
        return json.loads(rows[0]["doc"]) if rows else None

    def list(self) -> list[dict]:
        out = []
        for r in self.execute("SELECT id,source,title,artist,doc FROM tracks ORDER BY created DESC"):
            d = json.loads(r["doc"])
            out.append({"id": r["id"], "title": r["title"], "artist": r["artist"], "source": r["source"],
                        "bpm": d.get("bpm"), "key": d.get("key"), "key_alternatives": d.get("key_alternatives", [])})
        return out

    def vector(self, track_id: str, kind: str) -> np.ndarray | None:
        rows = self.execute("SELECT data FROM vectors WHERE track_id=? AND kind=?", (track_id, kind))
        return np.frombuffer(rows[0]["data"], dtype=np.float32) if rows else None

    def nearest(self, query: np.ndarray, kind: str, k: int = 5, exclude: str | None = None) -> list[tuple[str, float]]:
        """Top-k (track_id, cosine similarity) for one vector kind."""
        q = np.asarray(query, dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-12)
        scored = []
        for r in self.execute("SELECT track_id,data FROM vectors WHERE kind=?", (kind,)):
            if r["track_id"] == exclude:
                continue
            v = np.frombuffer(r["data"], dtype=np.float32)
            scored.append((r["track_id"], float(q @ (v / (np.linalg.norm(v) + 1e-12)))))
        return sorted(scored, key=lambda t: -t[1])[:k]
