"""Keeps song names right without the user doing anything.

  refresh(store, id)   re-derive one song's name and artist from its saved raw fields, now that the library
                       (and so the list of known artists) may know more than when it was first analysed;
  backfill(store)      songs analysed before the raw fields were kept: read them from SoundCloud once (no audio),
                       save them, then refresh everything. Runs in the background when the service starts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Callable

from . import download, names
from .store import Store


def refresh(store: Store, track_id: str) -> bool:
    """True if the song's name or artist changed."""
    doc = store.get(track_id)
    raw = (doc or {}).get("source_meta")
    if not raw or raw.get("unavailable"):
        return False
    song, artist = names.resolve(names.Meta(raw.get("title", ""), raw.get("uploader", ""), raw.get("artist", "")),
                                 names.known_artists(store, exclude_id=track_id))
    if (song, artist) == (doc.get("title"), doc.get("artist")):
        return False
    return store.update_track(track_id, title=song, artist=artist)


def backfill(store: Store, fetch: Callable[[str], names.Meta | None] = download.peek_meta, workers: int = 4) -> int:
    """Returns how many songs ended up with a different name or artist."""
    todo = [(r["id"], r["source"]) for r in store.execute("SELECT id, source FROM tracks")
            if str(r["source"]).startswith(("http://", "https://")) and "source_meta" not in (store.get(r["id"]) or {})]

    def get(item):
        try:
            return item[0], fetch(item[1])
        except Exception:                      # offline, or the track was taken down: try again next start
            return item[0], None

    with ThreadPoolExecutor(workers) as ex:
        for tid, meta in ex.map(get, todo):
            if meta:
                store.update_track(tid, source_meta=asdict(meta))
    return sum(refresh(store, r["id"]) for r in store.execute("SELECT id FROM tracks"))
