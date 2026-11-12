"""Answering a question about a song, a collection or a comparison, as a small LangGraph pipeline.

    route -> retrieve -> answer -> check --ok--------------------------> END
                                     | fixable, first time
                                     v
                                   repair -> check -> (ok | finalize) -> END

Interface: answer(llm, source, question, history) -> str.  A Source is the data about one subject (coach.py builds
them): a `brief` telling the model what the data is, and `context(topics)` returning only the part of it that the
question needs.

Every node is plain code except `answer` and `repair`, so a normal question costs one model call:
- route:    which parts of the data does the question touch (drums, bass, leads, ...)? A follow-up that names nothing
            inherits the topics of the question before it. Nothing recognised means "everything".
- retrieve: the system prompt = voice rules + the source's brief + only the routed slice of the data. A smaller, on-topic
            context is what keeps the model from drifting to unrelated numbers.
- check:    the answer must not narrate its source ("based on the data...", "the measurements show..."), must not say it
            cannot hear, and every number it quotes must be one the data contains (or an obvious difference, ratio or
            percentage of two). A failed check costs one more call (repair); if that still fails, the offending
            phrases are cut out in code, so the voice rules hold even when the model ignores them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, TypedDict

from langgraph.graph import END, StateGraph

from .llm import LLM

log = logging.getLogger("similar_sound.qa")

VOICE = (
    "Answer the question directly, the way someone who knows this track inside out would: lead with the answer, plain "
    "and specific. Never say where your information comes from: do not write 'based on', 'according to', 'the data', "
    "'the measurements', 'the analysis' or anything like them. State facts as facts ('The bass sits on E1 and F1'). "
    "Never say you cannot hear, listen to or see the audio, and never apologise for it. If something cannot be known "
    "(a patch's exact timbre, the exact notes of a chord stack) and the question asks for it, say so in one plain "
    "sentence about the thing itself, without mentioning data or measurements; do not add such caveats otherwise. "
    "Stay with what the track does and sounds like. Do not invent the producer's intentions or the track's purpose; when "
    "asked why something works, explain its musical effect and keep it clearly separate from the facts. No flattery or hype."
)

# what each word in a question points at. Longest-match is not needed: any hit adds the topic.
TOPICS: dict[str, tuple[str, ...]] = {
    "drums": ("drum*", "kick", "clap", "snare", "hat", "perc*", "ride", "shaker", "tom"),
    "groove": ("groove", "swing", "shuffle", "bounce", "timing", "feel", "rhythm", "vibe", "energy", "pocket"),
    "bass": ("bass*", "sub", "low end", "low-end", "808", "bottom"),
    "leads": ("lead", "stab", "pluck", "arp", "synth", "melod*", "riff", "hook", "top line", "topline", "keys"),
    "pads": ("pad", "chord", "atmos*", "drone", "texture*", "fx", "ambien*", "other"),
    "vocals": ("vocal*", "vox", "voice", "sample", "chop", "sing*", "acapella", "lyric*"),
    "mix": ("loud", "lufs", "master", "stereo", "width", "wide", "eq", "spectrum", "bright", "dark", "punch", "compress*",
            "dynamic*", "mix", "level", "clean", "muddy", "balance", "volume"),
    "loops": ("loop", "pattern", "bar", "repeat", "note", "step", "sequence", "placement", "layer", "rhythm"),
    "key": ("key", "scale", "harmon*", "minor", "major", "tonal*", "root"),
    "arrangement": ("build", "drop", "rise", "arrang*", "breakdown", "structure*", "section", "intro", "transition"),
    "similar": ("similar", "like", "closest", "resembl*", "compare", "reference", "alike", "sounds like"),
}
ALL = frozenset(TOPICS)


def _pattern(words: tuple[str, ...]) -> re.Pattern:
    """Whole words (with a plural or -ed/-ing ending); a trailing * means any word that starts that way. ("hat" must
    not match "that", nor "eq" match "equal".)"""
    parts = [re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) + r"(?:s|es|ed|ing)?" for w in words]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I)


_TOPIC_PATTERNS = {k: _pattern(v) for k, v in TOPICS.items()}
LAYER_OF_TOPIC = {"drums": "drums", "bass": "bass", "leads": "leads", "pads": "other", "vocals": "vocals"}


_OPEN = re.compile(r"\b(explain|overview|summar\w*|tell me about|describe (the )?(track|song|whole)|break ?down (the )?(track|song)|"
                   r"what('s| is) (going on|this (track|song)))\b", re.I)


def route(question: str, history: tuple = ()) -> frozenset[str]:
    """Topics the question touches. A question that names none (a follow-up like 'why?') takes those of the previous
    user turn; if there are none there either, everything."""
    def topics_of(text: str) -> set[str]:
        return {k for k, rx in _TOPIC_PATTERNS.items() if rx.search(text)}

    if _OPEN.search(question):                       # "explain this track": about everything, whatever came before
        return ALL
    found = topics_of(question)
    if not found and len(question.split()) <= 8:      # only a short follow-up ("why is that?") leans on the last question
        for role, text in reversed(history):
            if role == "user" and (found := topics_of(text)):
                break
    return frozenset(found) or ALL


# ---------------------------------------------------------------- the voice and honesty check
BANNED = [
    (re.compile(r"\bbased (on|off|upon)\b", re.I), "narrates its source ('based on...')"),
    (re.compile(r"\baccording to\b", re.I), "narrates its source ('according to...')"),
    (re.compile(r"\b(the|this|that|these|those|our|my)\s+(?:\w+\s+){0,2}(data|measurements?|analysis|readings?)\b", re.I),
     "mentions 'the data' / 'the measurements' / 'the analysis'"),
    (re.compile(r"\b(pulled|taken|drawn|extracted)\s+from\s+the\s+(audio|track|song)\b", re.I), "narrates its source ('pulled from the audio')"),
    (re.compile(r"\b(i|we)\s+(can('|no)t|cannot|can not|am unable to|'m unable to)\s+(actually\s+)?(hear|listen|see)\b", re.I),
     "says it cannot hear the audio"),
    (re.compile(r"\bas an ai\b", re.I), "says 'as an AI'"),
    (re.compile(r"\b(are|is|remain|remains)\s+(not|n't)\s+known\b|\bnot known\b", re.I), "adds an unrequested 'not known' caveat"),
]
_NUMBER = re.compile(r"(?<![\w.#])-?\d+(?:\.\d+)?")
# small whole numbers are steps, bars, counts and ordinals; they are not measurements worth checking
_FREE_INTEGER = 20


def _numbers(text: str) -> list[float]:
    return [float(m) for m in _NUMBER.findall(text)]


def unsupported_numbers(answer: str, context: str, history_text: str = "") -> list[str]:
    """Numbers in the answer that the data does not contain, directly or as a difference, ratio or percentage of two of
    its numbers. (Rounding is allowed.) Whole numbers up to 20 are free: bars, steps and counts."""
    have = sorted(set(_numbers(context) + _numbers(history_text)))
    near = lambda a, b: abs(a - b) <= max(0.06, 0.012 * abs(b))
    derived: set[float] = set()
    pool = [h for h in have if abs(h) < 1e5][:400]
    for i, a in enumerate(pool):
        for b in pool[i:]:
            derived.add(abs(a - b))
            derived.add(abs(a + b))
    bad = []
    for n in sorted(set(_numbers(answer))):
        if float(n).is_integer() and abs(n) <= _FREE_INTEGER:
            continue
        if any(near(n, h) or near(n, h * 100) or near(n, h / 100) or near(n, abs(h)) for h in have):
            continue
        if any(near(abs(n), d) or near(abs(n), d * 100) for d in derived):
            continue
        bad.append(f"{n:g}")
    return bad


def check(answer: str, context: str, history_text: str = "") -> list[str]:
    problems = [why for rx, why in BANNED if rx.search(answer)]
    nums = unsupported_numbers(answer, context, history_text)
    if nums:
        problems.append("quotes numbers that are not in the data: " + ", ".join(nums[:6]))
    return problems


def scrub(answer: str) -> str:
    """Last resort: cut the banned phrasing out of an answer the model would not fix. Drops leading clauses like
    'Based on the data, ' and any sentence that still narrates its source, unless that would leave nothing."""
    text = re.sub(r"^\s*(based (on|off|upon)|according to)\b[^,:;.]*[,:;]\s*", "", answer, flags=re.I)
    text = re.sub(r"^\s*(based (on|off|upon)|according to)\s+(the|this|that|these|our)\s+(audio\s+)?(data|measurements?|analysis)\s+", "", text, flags=re.I)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [s for s in sentences if not any(rx.search(s) for rx, _ in BANNED)]
    out = " ".join(kept) if kept else text
    return out[:1].upper() + out[1:]


# ---------------------------------------------------------------- the graph
@dataclass
class Source:
    brief: str                                     # what the data is about and how to answer
    context: Callable[[frozenset[str]], str]       # the data, restricted to the topics asked about


class State(TypedDict, total=False):
    question: str
    history: tuple
    topics: frozenset
    system: str
    context: str
    draft: str
    problems: list
    repaired: bool
    answer: str


def _build(llm: LLM, source: Source):
    def n_route(s: State) -> State:
        topics = route(s["question"], s["history"])
        log.info("question topics: %s", "everything" if topics == ALL else ", ".join(sorted(topics)))
        return {"topics": topics}

    def n_retrieve(s: State) -> State:
        ctx = source.context(s["topics"])
        return {"context": ctx, "system": f"{VOICE}\n\n{source.brief}\n\nData:\n{ctx}"}

    def n_answer(s: State) -> State:
        return {"draft": llm.complete(s["system"], s["question"], s["history"])}

    def n_check(s: State) -> State:
        hist = " ".join(t for _, t in s["history"])
        problems = check(s["draft"], s["context"], hist)
        if problems:
            log.info("answer check failed (%s): %s", "after repair" if s.get("repaired") else "first draft", "; ".join(problems))
        return {"problems": problems}

    def n_repair(s: State) -> State:
        fix = ("Rewrite your answer to the question below. Keep every fact that is supported by the data; fix these "
               "problems: " + "; ".join(s["problems"]) + ". Do not mention this rewrite.\n\n"
               f"Question: {s['question']}\n\nYour answer so far:\n{s['draft']}")
        return {"draft": llm.complete(s["system"], fix, ()), "repaired": True}

    def n_finalize(s: State) -> State:
        answer = scrub(s["draft"])
        if answer != s["draft"]:
            log.info("answer phrasing cut in code after the rewrite did not fix it")
        return {"answer": answer}

    def after_check(s: State) -> str:
        if not s["problems"]:
            return "done"
        return "finalize" if s.get("repaired") else "repair"

    g = StateGraph(State)
    for name, fn in (("route", n_route), ("retrieve", n_retrieve), ("answer", n_answer), ("check", n_check),
                     ("repair", n_repair), ("finalize", n_finalize), ("accept", lambda s: {"answer": s["draft"]})):
        g.add_node(name, fn)
    g.set_entry_point("route")
    g.add_edge("route", "retrieve")
    g.add_edge("retrieve", "answer")
    g.add_edge("answer", "check")
    g.add_conditional_edges("check", after_check, {"done": "accept", "repair": "repair", "finalize": "finalize"})
    g.add_edge("repair", "check")
    g.add_edge("accept", END)
    g.add_edge("finalize", END)
    return g.compile()


def answer(llm: LLM, source: Source, question: str, history: tuple = ()) -> str:
    final = _build(llm, source).invoke({"question": question, "history": tuple(history), "repaired": False})
    return final["answer"]
