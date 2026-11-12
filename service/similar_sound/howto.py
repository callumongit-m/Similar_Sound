"""Where things are in Ableton Live: verified steps for "how do I...?" questions.

The cards in live_manual/*.json are copied from Ableton's own manual (one file per Live version; only Live 12 so far),
so a click path the assistant gives is one the manual states, not one it half-remembers. They are only brought into
a conversation when the question asks how to do something (qa.py routes that), and the answer is checked: it may not
name a Live device or control that the retrieved cards do not contain.

Interface: is_howto(question), retrieve(query), render(cards, live_version), unverified_terms(answer, cards).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

MANUALS = Path(__file__).parent / "live_manual"
TOP_K = 3
MIN_SCORE = 3          # one keyword phrase found in the question, or several words in common

_HOWTO = re.compile(
    r"\b(how (do|can|could|should|would|will) (i|you|we)\b|how to\b|how'?s? (i|you) |where (is|are|do|can|would|should|does)\b|"
    r"which (button|menu|knob|setting|tab|panel|device|control)|what('s| is| are) the (button|setting|shortcut|menu|control)|"
    r"step[- ]by[- ]step|steps (to|for)\b|click\b|shortcut\b|set (it |this |that )?up\b|make sure (my|the|that) )", re.I)
_STOP = set("the a an and or of to in on for with is are do does how what where which can i my me it this that you your be get "
            "make sure tell show should would about from into have has".split())

# Live devices and controls a model might name. If an answer names one that is not in the retrieved steps, it is
# guessing a click path. (Plain words like "delay" or "compressor" are not listed: they are also ordinary English.)
NAMED_THINGS = [
    "Groove Pool", "Clip Groove", "Hot-Swap", "Global Amount", "Clip View", "Track Delay", "Utility", "EQ Eight",
    "Glue Compressor", "Auto Filter", "Drum Buss", "Roar", "Saturator", "Capture MIDI", "Drum Rack", "Simpler",
    "Limiter", "Legato", "Arpeggiator", "Velocity Editor", "Audio Utilities", "Quantize Settings", "MIDI Tools",
    "Channel EQ", "Multiband Dynamics", "Operator", "Wavetable", "Beat Repeat", "Redux", "Overdrive", "Gate",
    "Spectrum", "Chorus-Ensemble", "Phaser-Flanger", "Pedal", "Erosion", "Corpus", "Vocoder", "Resonators",
    "Mixer Config", "Mixer Controls", "Groove chooser", "Note Editor", "Control Bar",
    "Delay Compensation", "Arrangement Track Controls", "Transform panel", "Generate panel", "Warp switch",
]


@lru_cache
def _cards(version_major: str = "12") -> tuple[dict, ...]:
    path = MANUALS / f"live{version_major}.json"
    return tuple(json.loads(path.read_text())["cards"]) if path.exists() else ()


def is_howto(question: str) -> bool:
    return bool(_HOWTO.search(question))


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9/+#-]{3,}", text.lower()) if w not in _STOP}


def retrieve(query: str, version_major: str = "12", k: int = TOP_K) -> list[dict]:
    """The cards that best match the question, best first. A keyword phrase found in the question counts most."""
    q, qw = query.lower(), _words(query)
    scored = []
    for c in _cards(version_major):
        score = sum(3 for kw in c["keywords"] if re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", q))
        score += len(qw & _words(c["title"]))
        if score >= MIN_SCORE:
            scored.append((score, c))
    return [c for _, c in sorted(scored, key=lambda t: -t[0])[:k]]


def render(cards: list[dict], live_version: str | None) -> str:
    """The cards as the model sees them, with the user's Live version and what to do when nothing matches."""
    mine = f"The user has Live {live_version}." if live_version else "The user's Live version is unknown."
    other = (live_version or "12").split(".")[0] != "12"
    head = (f"Ableton steps. {mine} These steps are from the Live 12 manual"
            + (": menus may differ in their version, so say so." if other else ".")
            + " Give the click path in order, naming menus and buttons exactly as written here. Use only these steps: "
              "if they do not cover what was asked, say you are not sure where that control is instead of guessing.")
    if not cards:
        return head + "\n(No stored steps match this question.)"
    body = []
    for c in cards:
        lines = [f"## {c['title']}", *[f"{i}. {s}" for i, s in enumerate(c["steps"], 1)]]
        lines += [f"- {n}" for n in c.get("notes", [])]
        if c.get("confidence") != "manual":
            lines.append("(Lower confidence: summarised from the manual, not copied.)")
        body.append("\n".join(lines))
    return head + "\n\n" + "\n\n".join(body)


def unverified_terms(answer: str, cards: list[dict]) -> list[str]:
    """Live devices/controls the answer names that none of the retrieved cards contain."""
    text = " ".join(f"{c['title']} {' '.join(c['steps'])} {' '.join(c.get('notes', []))}" for c in cards).lower()
    return [t for t in NAMED_THINGS if re.search(rf"\b{re.escape(t)}\b", answer) and t.lower() not in text]
