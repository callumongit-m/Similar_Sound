"""Plain-English answers. Numbers are computed deterministically (gaps()); the LLM only narrates.

ask(track)      : explain one library track (what makes it tick, how to build the same feel).
compare(yours)  : rank where your bounce differs from its nearest references, then explain.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from .features import BAND_NAMES
from .llm import LLM
from .store import Store

SYSTEM = (
    "You coach a beginner (1 month in) making UK minimal tech house in Ableton. You only see measured "
    "numbers extracted from audio, never the audio itself: say so when the data cannot answer something. "
    "Be concrete and plain-spoken: name the instrument/bus, the direction and rough size of the change, and "
    "one Ableton move to try (device, setting). No jargon without a short explanation. Prioritise the 3 biggest "
    "gaps, not everything. Hedge honestly: groove and sound choice are partly beyond these measurements."
)


@dataclass
class Gap:
    label: str
    yours: float
    reference: float
    delta: float
    score: float  # |delta| / typical-scale; comparable across metrics


def _flat(doc: dict) -> dict[str, tuple[float, float]]:
    """metric label -> (value, typical scale). Scale = the size of a difference worth noticing."""
    m: dict[str, tuple[float, float]] = {}
    mix = doc["mix"]
    m["mix loudness (LUFS)"] = (mix["lufs"], 1.5)
    m["mix crest factor (dB, higher = punchier)"] = (mix["crest_db"], 1.5)
    for n, v in zip(BAND_NAMES, mix["band_energy_db"]):
        m[f"mix {n} balance (dB)"] = (v, 3.0)
    for n, v in zip(("low", "mid", "high"), mix["stereo_width"]):
        m[f"mix {n} stereo width"] = (v, 0.1)
    for stem, s in doc["stems"].items():
        m[f"{stem} brightness (Hz centroid)"] = (s["centroid_hz"], 600.0 if stem != "bass" else 150.0)
        for n, v in zip(BAND_NAMES, s["band_energy_db"]):
            m[f"{stem} {n} balance (dB)"] = (v, 4.0)
        for n, v in zip(("low", "mid", "high"), s["stereo_width"]):
            m[f"{stem} {n} stereo width"] = (v, 0.12)
    g = doc["groove"]
    if not g.get("empty"):
        m["groove swing (fraction of a 16th)"] = (g["swing"], 0.05)
        for band in ("kick", "mid", "hat"):
            m[f"{band} hits per bar"] = (g[band]["density_per_bar"], 2.0)
    a = doc["arrangement"]
    m["build->drop energy rise (dB)"] = (a["rise_db"], 2.0)
    for stem, v in a["stem_rise_db"].items():
        m[f"{stem} enters/rises at drop (dB)"] = (v, 3.0)
    return m


def gaps(yours: dict, refs: list[dict], top: int = 8) -> list[Gap]:
    """Where `yours` differs from the mean of `refs`, biggest first."""
    mine = _flat(yours)
    ref_flats = [_flat(r) for r in refs]
    out = []
    for label, (v, scale) in mine.items():
        vals = [f[label][0] for f in ref_flats if label in f]
        if not vals:
            continue
        ref = float(np.mean(vals))
        out.append(Gap(label, round(v, 3), round(ref, 3), round(v - ref, 3), abs(v - ref) / scale))
    return sorted(out, key=lambda g: -g.score)[:top]


def _summary(doc: dict) -> str:
    g = doc["groove"]
    slim = {k: doc[k] for k in ("title", "artist", "bpm", "key", "mix", "stems", "arrangement", "detection_confidence")
            if k in doc}
    slim["groove"] = {k: v for k, v in g.items() if k != "vector"}
    return json.dumps(slim, separators=(",", ":"))


def _neighbours(store: Store, track_id: str, k: int) -> list[dict]:
    seen, out = set(), []
    for kind in ("groove", "mix"):
        v = store.vector(track_id, kind)
        if v is None:
            continue
        for tid, score in store.nearest(v, kind, k, exclude=track_id):
            if tid not in seen and (d := store.get(tid)):
                seen.add(tid)
                out.append({"title": d.get("title"), "similar_by": kind, "score": round(score, 3)})
    return out


def ask(store: Store, llm: LLM, track_id: str, question: str) -> str:
    doc = store.get(track_id)
    if doc is None:
        raise KeyError(track_id)
    prompt = (f"Measured data for the build+drop of this reference track:\n{_summary(doc)}\n\n"
              f"Closest tracks in the library: {json.dumps(_neighbours(store, track_id, 3))}\n\n"
              f"Question: {question}")
    return llm.complete(SYSTEM, prompt)


def compare(store: Store, llm: LLM, yours: dict, your_vectors: dict[str, np.ndarray], k: int = 3) -> dict:
    """Your bounce is analysed like a reference but not saved; it is diffed against its groove-nearest tracks."""
    near = store.nearest(your_vectors["groove"], "groove", k)
    refs = [d for tid, _ in near if (d := store.get(tid))]
    if not refs:
        raise ValueError("library is empty: ingest some references first")
    found = gaps(yours, refs)
    table = [{"metric": g.label, "yours": g.yours, "references_avg": g.reference, "delta": g.delta} for g in found]
    prompt = (f"Reference tracks used: {[r.get('title') for r in refs]}\n"
              f"Biggest measured gaps (yours vs reference average, ranked):\n{json.dumps(table)}\n\n"
              "Explain the top gaps in plain English and what to try in Ableton.")
    return {"answer": llm.complete(SYSTEM, prompt), "gaps": table, "references": [r.get("title") for r in refs]}
