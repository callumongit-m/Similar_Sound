"""Plain-English answers. Numbers are computed deterministically (gaps()); the LLM only narrates.

ask(track)      : explain one library track (what makes it tick, how to build the same feel).
compare(yours)  : rank where your bounce differs from its nearest references, then explain.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .features import BAND_NAMES
from .llm import LLM
from .store import Store

SYSTEM = (
    "You coach a beginner (1 month in) making UK minimal tech house in Ableton. Everything you know comes from "
    "measurements pulled from the audio. Say so by framing what you tell them as coming from that data, with "
    "phrases like 'Based off the data pulled from the audio...', 'The measurements taken from the audio show...' "
    "or 'According to the data from the audio...' (vary the wording, do not repeat the same opener). NEVER say you "
    "cannot hear, listen to, or see the audio, never describe that as a limitation and never apologise for it. "
    "When the data cannot answer something (a patch's exact timbre, sound choice), say 'the data pulled from the "
    "audio can't tell us that' instead. "
    "Be concrete and plain-spoken: name the instrument/bus, the direction and rough size of the change, and "
    "one Ableton move to try (device, setting). No jargon without a short explanation. Prioritise the 3 biggest "
    "gaps, not everything. Hedge honestly: groove and sound choice are partly beyond these measurements. "
    "Stems: 'drums', 'bass' and 'other' (synths, leads, pads, vocals: everything that is neither drums nor bass). "
    "If 'key_could_also_be' is present the key is a close call between those keys: say so rather than naming one. "
    "'loops' (when present) is what each layer plays in the drop, read bar by bar over 16 steps: the bass line is "
    "reliable note by note; the synth layer is only the steps it hits and the pitch classes sounding there (a sketch of "
    "its rhythm and harmony, NOT a transcription of its melody); say so if asked for exact synth notes. A layer marked "
    "absent_in_the_drop is not playing in the drop: say that. If 'loops' is missing the song was analysed before loops "
    "were measured: say that song has no loop data yet rather than guessing. "
    "Quote numbers exactly as given and never invent a measurement that is not in the data. Loudness differences "
    "between an unmastered project and a mastered reference reflect mastering, not mixing: say so."
)


@dataclass
class Gap:
    label: str
    yours: float
    reference: float   # mean over the reference songs
    delta: float
    score: float       # |delta| / typical scale, boosted when outside every reference song; comparable across metrics
    low: float         # lowest / highest reference value
    high: float
    n: int
    outside: bool      # beyond every reference song (needs 2+)
    text: str          # the line the window shows


STEMS = {"drums": "Drums", "bass": "Bass", "other": "Synths/other"}
LANES = {"kick": "kick", "mid": "mid-range percussion", "hat": "hats"}
WIDTHS = ("low end", "mids", "highs")


def _flat(doc: dict) -> dict[str, tuple[float, float]]:
    """metric label -> (value, typical scale). Scale = the size of a difference worth noticing."""
    m: dict[str, tuple[float, float]] = {}
    mix = doc["mix"]
    m["Mix · loudness (LUFS)"] = (mix["lufs"], 1.5)
    m["Mix · punch (crest factor, dB)"] = (mix["crest_db"], 1.5)
    for n, v in zip(BAND_NAMES, mix["band_energy_db"]):
        m[f"Mix · {n.replace('_', ' ')} level (dB)"] = (v, 3.0)
    for n, v in zip(WIDTHS, mix["stereo_width"]):
        m[f"Mix · {n} stereo width"] = (v, 0.1)
    for stem, s in doc["stems"].items():
        name = STEMS.get(stem, stem)
        m[f"{name} · brightness (Hz)"] = (s["centroid_hz"], 600.0 if stem != "bass" else 150.0)
        for n, v in zip(BAND_NAMES, s["band_energy_db"]):
            m[f"{name} · {n.replace('_', ' ')} level (dB)"] = (v, 4.0)
        for n, v in zip(WIDTHS, s["stereo_width"]):
            m[f"{name} · {n} stereo width"] = (v, 0.12)
    g = doc["groove"]
    if not g.get("empty"):
        m["Groove · swing"] = (g["swing"], 0.05)
        for lane, label in LANES.items():
            m[f"Groove · {label} hits per bar"] = (g[lane]["density_per_bar"], 2.0)
    a = doc.get("arrangement")
    if a:
        m["Build to drop · energy rise (dB)"] = (a["rise_db"], 2.0)
        for stem, v in a["stem_rise_db"].items():
            m[f"{STEMS.get(stem, stem)} · rise at the drop (dB)"] = (v, 3.0)
    return m


def _num(v: float) -> str:
    return f"{v:.2f}" if abs(v) < 1 else f"{v:.1f}" if abs(v) < 100 else f"{v:.0f}"


def gaps(yours: dict, refs: list[dict], top: int = 8, ref_name: str = "the reference") -> list[Gap]:
    """Where `yours` sits against the reference songs, biggest difference first. Being outside the range
    of every reference song counts for more than being off their average."""
    mine = _flat(yours)
    ref_flats = [_flat(r) for r in refs]
    out = []
    for label, (v, scale) in mine.items():
        vals = [f[label][0] for f in ref_flats if label in f]
        if not vals:
            continue
        ref, lo, hi, n = float(np.mean(vals)), min(vals), max(vals), len(vals)
        outside = n >= 2 and (v < lo or v > hi)
        if n == 1:
            text = f"{label}: yours {_num(v)}, {ref_name} {_num(ref)}"
        else:
            where = f" (below all {n})" if v < lo and outside else f" (above all {n})" if v > hi and outside else ""
            text = f"{label}: yours {_num(v)}; {ref_name} averages {_num(ref)}, range {_num(lo)} to {_num(hi)}{where}"
        out.append(Gap(label, round(v, 3), round(ref, 3), round(v - ref, 3), abs(v - ref) / scale * (1.4 if outside else 1.0),
                       round(lo, 3), round(hi, 3), n, outside, text))
    return sorted(out, key=lambda g: -g.score)[:top]


LOOPS_HOW_TO_READ = (
    "Each pattern string is one bar of 16 steps, step 0 first (step 0 is the kick's beat; steps 0, 4, 8, 12 are the "
    "quarter-note beats, odd steps the off-beat 16ths). Bass: a note name where a note starts, '~' where it carries "
    "on, '.' where silent. Synth: the pitch classes sounding where a hit lands ('E+G+B'), '.' elsewhere. Drums: k = kick, "
    "m = mid-range percussion, h = hats. 'repeats_every_bars' is the shortest loop length that matches; "
    "'bars_matching' is how closely later bars repeat it (1 = identical).")


def _loops(doc: dict) -> dict:
    """The drop's loops for the model, or the reason there are none (songs analysed before loops existed)."""
    lp = doc.get("loops")
    if lp is None:
        return {"not_measured_for_this_song": "analysed before loops were measured"}
    if lp.get("empty"):
        return {"not_measurable": "no steady beat grid was found in the drop"}
    out = {"how_to_read": LOOPS_HOW_TO_READ, "bars_analysed": lp["bars_analysed"],
           "whole_groove_repeats_every_bars": lp["whole_groove_repeats_every_bars"]}
    for layer in ("bass", "other", "drums"):
        out["synth_and_other" if layer == "other" else layer] = lp[layer]
    out["what_lands_on_each_16th_step"] = lp["what_lands_on_each_16th_step"]
    return out


BAND_HZ = {"sub": "20-60 Hz", "bass": "60-120 Hz", "low_mid": "120-250 Hz", "mid": "250-500 Hz",
           "upper_mid": "500 Hz-2 kHz", "presence": "2-6 kHz", "air": "6-12 kHz", "top": "12-20 kHz"}
WIDTH_RANGES = ("below 150 Hz", "150 Hz-2 kHz", "above 2 kHz")


def labelled(doc: dict) -> dict:
    """Every number named and unit-ed. The raw JSON stores bare lists (8 band levels, 16 step hits); an LLM
    shown those can silently misread which entry is sub and which is air. This is what it actually sees."""
    def bands(levels):
        return {f"{n} ({BAND_HZ[n]})": v for n, v in zip(BAND_NAMES, levels)}

    def stem(m):
        return {"loudness_dB": m["rms_db"], "brightness_centroid_Hz": m["centroid_hz"],
                "spectrum_dB_relative_to_its_own_total": bands(m["band_energy_db"]),
                "stereo_width_0_mono_to_1_wide": dict(zip(WIDTH_RANGES, m["stereo_width"]))}

    g = doc["groove"]
    groove = {"empty": True} if g.get("empty") else {
        "swing_fraction_of_a_16th_note_positive_means_offbeats_late": g["swing"],
        "lanes": {lane: {"hits_per_bar": g[lane]["density_per_bar"],
                         "16th_steps_hit_0_to_15_counting_from_the_kick_beat": [i for i, h in enumerate(g[lane]["hit"]) if h > 0.5]}
                  for lane in ("kick", "mid", "hat")}}
    a = doc.get("arrangement") or {}
    out = {
        "title": doc.get("title"), "artist": doc.get("artist"), "bpm": doc["bpm"], "key": doc["key"],
        **({"key_could_also_be": doc["key_alternatives"]} if doc.get("key_alternatives") else {}),
        "full_mix": {"loudness_LUFS": doc["mix"]["lufs"], "crest_factor_dB_higher_is_punchier": doc["mix"]["crest_db"],
                     "spectrum_dB_relative_to_total": bands(doc["mix"]["band_energy_db"]),
                     "stereo_width_0_mono_to_1_wide": dict(zip(WIDTH_RANGES, doc["mix"]["stereo_width"]))},
        "stems": {name: stem(m) for name, m in doc["stems"].items()},
        "groove": groove,
        "loops": _loops(doc),
    }
    if a:
        out["build_to_drop"] = {"loudness_rise_dB": a.get("rise_db"), "each_stem_rise_dB": a.get("stem_rise_db")}
    return out


def _summary(doc: dict) -> str:
    return json.dumps(labelled(doc), separators=(",", ":"))


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


def ask(store: Store, llm: LLM, track_id: str, question: str, history: tuple = ()) -> str:
    doc = store.get(track_id)
    if doc is None:
        raise KeyError(track_id)
    context = ("This is a REFERENCE track the user is studying to understand it. It is not their own project, so do not "
               "coach them or list fixes unless they ask for that. Answer exactly the question asked, in a few plain "
               "sentences, quoting the measured numbers. Frame it as coming from the audio's data ('Based off the data "
               "pulled from the audio...'), never as something you failed to hear. No other preamble or disclaimers. "
               "If they ask a follow-up, use the conversation so far.\n\n"
               f"Measured data for the build+drop of this reference track:\n{_summary(doc)}\n\n"
               f"Closest tracks in the library: {json.dumps(_neighbours(store, track_id, 3))}")
    return llm.complete(f"{SYSTEM}\n\n{context}", question, history)


SMALL_COLLECTION = 5


# which measured differences explain which part of the feel
ASPECT_GAPS = {"mix": ("Mix ·",), "groove": ("Groove ·",), "drums": ("Drums ·",), "bass": ("Bass ·",), "other": ("Synths/other ·",)}


def related_gaps(all_gaps: list[Gap], kind: str, n: int = 3) -> list[dict]:
    """The biggest measured differences behind one aspect of the feel (already sorted biggest first)."""
    prefixes = ASPECT_GAPS[kind]
    return [{"label": g.label, "text": g.text, "delta": g.delta, "outside": g.outside}
            for g in all_gaps if g.label.startswith(prefixes)][:n]


def compare_project(yours: dict, refs: list[dict], ref_name: str, is_collection: bool) -> dict:
    """Deterministic comparison of the captured project against a song or a collection."""
    if not refs:
        raise ValueError("There are no songs in that collection yet.")
    everything = gaps(yours, refs, top=10_000, ref_name=ref_name)
    found = everything[:8]
    small = is_collection and len(refs) < SMALL_COLLECTION
    return {
        "reference": ref_name, "n": len(refs), "is_collection": is_collection, "small": small,
        "note": (f"{ref_name} only has {len(refs)} song{'s' if len(refs) != 1 else ''}, so treat this as a rough guide."
                 if small else ""),
        "references": [r.get("title") for r in refs],
        "gaps": [g.__dict__ for g in found],
        "why": {kind: related_gaps(everything, kind) for kind in ASPECT_GAPS},
    }


def ask_about_comparison(llm: LLM, project: dict, comparison: dict, question: str, history: tuple = ()) -> str:
    table = [{"metric": g["label"], "yours": g["yours"], "reference_average": g["reference"],
              "reference_range": [g["low"], g["high"]], "beyond_every_reference_song": g["outside"]}
             for g in comparison["gaps"]]
    feel = comparison.get("feel")
    feel_text = ""
    if feel and feel["aspects"]:
        rows = [{"aspect": a["label"], "closeness_0_to_100": a["score"], "typical_for_the_references": a["typical"],
                 "how_much_further_than_typical": a["gap"], "closest": a["closest"], "furthest": a["furthest"]} for a in feel["aspects"]]
        why = comparison["why"].get(feel["weakest"], []) if feel["weakest"] else []
        feel_text = ("HOW IT FEELS (measured with audio embeddings, i.e. how a listener hears it, not the numbers below):\n"
                     f"{json.dumps(rows, ensure_ascii=False)}\n"
                     "closeness_0_to_100 is how close your project's feel is to the references. It is only meaningful next to "
                     "'typical_for_the_references', which is how close the reference songs are to each other: "
                     "'how_much_further_than_typical' above 0 means your project is further from them than they are from one another. "
                     f"{'The furthest aspect is ' + feel['weakest'] + '. Measured differences behind it: ' + json.dumps([w['text'] for w in why], ensure_ascii=False) if feel['weakest'] else ''}\n"
                     f"{'' if feel['reliable'] else 'Caveat: ' + feel['note']}\n\n")
    prompt = (f"This is the user's own work-in-progress, captured live from Ableton, compared against "
              f"{comparison['reference']} ({comparison['n']} song(s): {comparison['references']}).\n"
              f"{comparison['note']}\n\n"
              f"Their project, measured:\n{_summary(project)}\n\n"
              f"{feel_text}"
              f"The biggest measured differences, most important first:\n{json.dumps(table, ensure_ascii=False)}\n\n"
              "If they ask a follow-up, use the conversation so far.")
    return llm.complete(f"{SYSTEM}\n\n{prompt}", question, history)


# ---------------------------------------------------------------- a whole collection
MAX_SONG_LINES = 40            # per-song lines in the digest; beyond that the statistics still use every song
SHARED_TRAITS = 10
VARIABLE_TRAITS = 6
COLLECTION_SYSTEM_NOTE = (
    "This is a COLLECTION: reference songs the user grouped as one sub-genre, measured over each song's main build "
    "and drop. They want to understand what the songs HAVE IN COMMON and what makes the style work. Lead with traits "
    "most songs share (the 'most_shared_traits' and 'drum_patterns' below), then name any clear outliers. Quote the "
    "numbers and say how many songs they hold for. Frame it as coming from the data ('Based off the data pulled from "
    "the audio...'). The data has each song's bass line (notes per 16th step), how many bars each layer takes to repeat, and which steps the synths hit with their pitch classes (a sketch, not a transcription): see 'loop_patterns'. It does NOT include patch or sound-design settings or exact synth melodies: for those, say what the data does show and that the data pulled from the audio can't tell us the rest. "
    "If they ask a follow-up, use the conversation so far."
)


def _stat_rows(docs: list[dict]) -> list[dict]:
    flats = [_flat(d) for d in docs]
    rows = []
    for label in flats[0]:
        vals = [f[label][0] for f in flats if label in f]
        if len(vals) < max(2, len(docs) // 2):
            continue
        if label.endswith("level (dB)") and max(vals) < -40:        # a band nothing lives in: consistent, but means nothing
            continue
        scale = flats[0][label][1]
        rows.append({"metric": label, "mean": round(float(np.mean(vals)), 2), "range": [round(min(vals), 2), round(max(vals), 2)],
                     "songs": len(vals), "spread": float(np.std(vals)) / scale})
    return rows


def _step_patterns(docs: list[dict]) -> dict:
    out = {}
    for lane in ("kick", "mid", "hat"):
        hits = [d["groove"][lane]["hit"] for d in docs if not d["groove"].get("empty")]
        if not hits:
            continue
        freq = np.mean(np.array(hits) > 0.5, axis=0)
        out[LANES[lane]] = {"steps_hit_in_70_percent_or_more_of_songs": [i for i, f in enumerate(freq) if f >= 0.7],
                            "steps_hit_in_30_to_70_percent_of_songs": [i for i, f in enumerate(freq) if 0.3 <= f < 0.7]}
    return out


def _loop_patterns(docs: list[dict]) -> dict:
    """What the songs' bass lines and layers have in common, from the songs that have loop data."""
    lps = [d["loops"] for d in docs if d.get("loops") and not d["loops"].get("empty")]
    out: dict = {"songs_with_loop_data": len(lps), "of": len(docs)}
    if not lps:
        return out
    present = lambda layer: [lp[layer] for lp in lps if not lp[layer].get("absent_in_the_drop")]
    bass = present("bass")
    out["bass"] = {"songs_with_bass_in_the_drop": len(bass)}
    if bass:
        out["bass"].update({
            "most_used_notes_across_songs": dict(Counter(n for b in bass for n in list(b.get("notes_by_share", {}))[:2]).most_common(5)),
            "loop_length_in_bars_counts": dict(Counter(b["repeats_every_bars"] for b in bass)),
            "average_sounding_steps_per_bar": round(float(np.mean([b.get("sounding_steps_per_bar", 0) for b in bass])), 1)})
    other = present("other")
    if other:
        out["synth_and_other"] = {"songs_with_it_in_the_drop": len(other),
                                  "average_hits_per_bar": round(float(np.mean([o["hits_per_bar"] for o in other])), 1),
                                  "loop_length_in_bars_counts": dict(Counter(o["repeats_every_bars"] for o in other))}
    out["whole_groove_loop_length_in_bars_counts"] = dict(Counter(lp["whole_groove_repeats_every_bars"] for lp in lps))
    placed = {}
    for layer in ("kick", "mid-percussion", "hat", "bass", "synth"):
        freq = np.mean([[layer in step.split(": ", 1)[1].split("+") for step in lp["what_lands_on_each_16th_step"]]
                        for lp in lps], axis=0)
        placed[layer] = [i for i, f in enumerate(freq) if f >= 0.7]
    out["steps_each_layer_lands_on_in_70_percent_or_more_of_songs"] = placed
    return out


def collection_digest(name: str, docs: list[dict]) -> str:
    """Everything the model needs to answer questions about a whole collection, in a size that does not grow with it:
    per-song headlines (the first MAX_SONG_LINES), then statistics over ALL the songs: which measured traits they share,
    which vary, and the drum step patterns most of them use."""
    rows = sorted(_stat_rows(docs), key=lambda r: r["spread"])
    strip = lambda r: {k: v for k, v in r.items() if k != "spread"}
    bpms = [d["bpm"] for d in docs]
    keys: dict[str, int] = {}
    for d in docs:
        keys[d["key"]] = keys.get(d["key"], 0) + 1
    out = {
        "collection": name, "songs_count": len(docs),
        "bpm": {"mean": round(float(np.mean(bpms)), 1), "range": [round(min(bpms), 1), round(max(bpms), 1)]},
        "keys_of_the_songs": dict(sorted(keys.items(), key=lambda kv: -kv[1])),
        "most_shared_traits": [strip(r) for r in rows[:SHARED_TRAITS]],
        "most_variable_traits": [strip(r) for r in rows[::-1][:VARIABLE_TRAITS]],
        "drum_patterns_16th_steps_0_to_15": _step_patterns(docs),
        "loop_patterns": _loop_patterns(docs),
        "songs": [{"title": d.get("title"), "artist": d.get("artist"), "bpm": round(d["bpm"]), "key": d["key"],
                   **({"swing": d["groove"]["swing"]} if not d["groove"].get("empty") else {}), "lufs": d["mix"]["lufs"],
                   **({"bass_bar_1": d["loops"]["bass"]["loop"][0]} if (d.get("loops") or {}).get("bass", {}).get("loop") else {})}
                  for d in docs[:MAX_SONG_LINES]],
    }
    if len(docs) > MAX_SONG_LINES:
        out["more_songs_not_listed"] = len(docs) - MAX_SONG_LINES
    return json.dumps(out, separators=(",", ":"), ensure_ascii=False)


def ask_collection(store: Store, llm: LLM, name: str, docs: list[dict], question: str, history: tuple = ()) -> str:
    if not docs:
        raise ValueError("There are no songs in that collection yet.")
    context = (f"{COLLECTION_SYSTEM_NOTE}\n\nMeasured data for the collection:\n{collection_digest(name, docs)}\n\n"
               f"{_legend()}")
    return llm.complete(f"{SYSTEM}\n\n{context}", question, history)


def _legend() -> str:
    return ("How to read the metrics: levels are dB relative to the stem's own total; stereo width is 0 (mono) to 1 (wide); "
            "brightness is the spectral centroid in Hz; groove swing is the fraction of a 16th note that off-beats are "
            "late (positive = late); 'rise at the drop' is how much louder that stem is in the drop than in the build.")
