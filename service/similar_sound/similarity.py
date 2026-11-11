"""How close does your project *feel* to the reference songs?

The vibe vectors (CLAP embeddings of the whole mix and of each stem) and the groove vector already exist for
every library song. A raw cosine between two of them is not a usable score on its own: it is 0.86 between two
songs' basses and 0.65 between their synths, so "0.65" does not mean "bad". Each aspect needs its own yardstick.

The yardstick used here is how close the reference songs are to *each other* (a collection's own spread).
`gap` = how much further your project is from the references than they are from one another: the larger it is,
the more that aspect of the feel differs. That ranks the aspects fairly, and says which one to work on first.
With few songs the yardstick is only a handful of pairs, so `reliable` says when to trust it.
"""
from __future__ import annotations

import itertools

import numpy as np

from .store import Store

ASPECTS = (("mix", "Overall vibe"), ("groove", "Groove"), ("drums", "Drums"), ("bass", "Bass"), ("other", "Synths"))
MIN_PAIRS_RELIABLE = 3


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def _pairwise(store: Store, kind: str, ids: list[str]) -> list[float]:
    vs = [v for i in ids if (v := store.vector(i, kind)) is not None]
    return [_cos(a, b) for a, b in itertools.combinations(vs, 2)]


def closeness(store: Store, project: dict[str, np.ndarray], ref_ids: list[str], titles: dict[str, str]) -> dict:
    """project: the captured project's vectors by kind. ref_ids: the songs being compared with."""
    library_ids = [t["id"] for t in store.list()]
    aspects = []
    for kind, label in ASPECTS:
        if kind not in project:
            continue
        per_ref = [(i, _cos(project[kind], v)) for i in ref_ids if (v := store.vector(i, kind)) is not None]
        if not per_ref:
            continue
        mean = float(np.mean([s for _, s in per_ref]))
        # the yardstick: how close the references are to each other; a single reference borrows the library's spread
        pairs, scope = _pairwise(store, kind, ref_ids), "these songs"
        if not pairs:
            pairs, scope = _pairwise(store, kind, library_ids), "your library"
        typical = float(np.mean(pairs)) if pairs else None
        closest = max(per_ref, key=lambda x: x[1]); furthest = min(per_ref, key=lambda x: x[1])
        aspects.append({
            "kind": kind, "label": label,
            "score": round(100 * mean), "typical": None if typical is None else round(100 * typical),
            "gap": None if typical is None else round(100 * (typical - mean), 1),
            "pairs": len(pairs), "scope": scope if pairs else None,
            "closest": {"title": titles.get(closest[0], closest[0]), "score": round(100 * closest[1])} if len(per_ref) > 1 else None,
            "furthest": {"title": titles.get(furthest[0], furthest[0]), "score": round(100 * furthest[1])} if len(per_ref) > 1 else None,
        })
    ranked = [a for a in aspects if a["gap"] is not None and a["kind"] != "mix"]   # "Overall vibe" is the summary, not a thing to fix
    weakest = max(ranked, key=lambda a: a["gap"])["kind"] if ranked and max(a["gap"] for a in ranked) > 0 else None
    pairs = min((a["pairs"] for a in aspects), default=0)
    return {"aspects": aspects, "weakest": weakest, "pairs": pairs, "reliable": pairs >= MIN_PAIRS_RELIABLE,
            "note": "" if pairs >= MIN_PAIRS_RELIABLE else
                    ("Add more songs for a sharper read: with only "
                     f"{pairs} pair{'s' if pairs != 1 else ''} of reference songs to measure against, this is a rough guide."
                     if pairs else "Add more songs so I have something to measure the feel against.")}
