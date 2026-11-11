"""Collections: the user's own sub-genre folders. A song can sit in several, or none ("unsorted").

Deleting a collection never deletes songs. Everything here is a thin, validated layer over two tables.
"""
from __future__ import annotations

import sqlite3
import time

from .store import Store

MAX_NAME = 40


class LibraryError(Exception):
    """Carries the sentence to show the user."""


def _clean(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise LibraryError("Give the collection a name.")
    return name[:MAX_NAME]


def create(store: Store, name: str) -> dict:
    name = _clean(name)
    try:
        store.execute("INSERT INTO collections(name, created) VALUES (?,?)", (name, time.time()))
    except sqlite3.IntegrityError:
        raise LibraryError(f"You already have a collection called {name}.") from None
    return {"id": store.execute("SELECT id FROM collections WHERE name=?", (name,))[0]["id"], "name": name, "track_ids": []}


def rename(store: Store, collection_id: int, name: str) -> None:
    name = _clean(name)
    _require(store, collection_id)
    try:
        store.execute("UPDATE collections SET name=? WHERE id=?", (name, collection_id))
    except sqlite3.IntegrityError:
        raise LibraryError(f"You already have a collection called {name}.") from None


def delete(store: Store, collection_id: int) -> None:
    _require(store, collection_id)
    store.execute("DELETE FROM collection_tracks WHERE collection_id=?", (collection_id,))
    store.execute("DELETE FROM collections WHERE id=?", (collection_id,))


def add(store: Store, collection_id: int, track_id: str) -> None:
    """Idempotent: dropping a song into a folder it is already in changes nothing."""
    _require(store, collection_id)
    if store.get(track_id) is None:
        raise LibraryError("That song isn't in your library.")
    store.execute("INSERT OR IGNORE INTO collection_tracks(collection_id, track_id, added) VALUES (?,?,?)",
                  (collection_id, track_id, time.time()))


def remove(store: Store, collection_id: int, track_id: str) -> None:
    store.execute("DELETE FROM collection_tracks WHERE collection_id=? AND track_id=?", (collection_id, track_id))


def list_all(store: Store) -> list[dict]:
    out = []
    for c in store.execute("SELECT id, name FROM collections ORDER BY name COLLATE NOCASE"):
        ids = [r["track_id"] for r in store.execute(
            "SELECT track_id FROM collection_tracks WHERE collection_id=? ORDER BY added", (c["id"],))]
        out.append({"id": c["id"], "name": c["name"], "track_ids": ids})
    return out


def docs(store: Store, collection_id: int) -> list[dict]:
    """The full analysis of every song in a collection (the reference set for a comparison)."""
    _require(store, collection_id)
    return [d for r in store.execute("SELECT track_id FROM collection_tracks WHERE collection_id=?", (collection_id,))
            if (d := store.get(r["track_id"]))]


def name_of(store: Store, collection_id: int) -> str:
    return _require(store, collection_id)["name"]


def _require(store: Store, collection_id: int):
    rows = store.execute("SELECT id, name FROM collections WHERE id=?", (collection_id,))
    if not rows:
        raise LibraryError("That collection doesn't exist.")
    return rows[0]
