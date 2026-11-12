"""Real-model check of the question-answering graph: does it hold the voice rules and quote only real numbers?

Uses the key in the Keychain and a handful of real calls. A fresh song is analysed (audio is deleted after) so the
loops and five-layer data are the new schema. Run from service/:  PYTHONPATH=. uv run python evals/qa_real.py <url>
"""
import sys

from similar_sound import coach, config, llm, pipeline, qa, store as store_mod

QUESTIONS = [
    "what is the bass doing?", "what notes does the bassline play and how long is the loop?",
    "describe the drums and the groove", "are there vocals or vocal chops?",
    "what are the leads and pads doing?", "explain this track to me", "why is that?",
    "how loud is it and how wide is the stereo image?",
]


class Counting:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def complete(self, system, prompt, history=()):
        self.calls += 1
        return self.inner.complete(system, prompt, history)


if __name__ == "__main__":
    s = store_mod.Store(":memory:")
    rec = pipeline.ingest(sys.argv[1])
    s.add(rec.doc, rec.vectors)
    tid = rec.doc["id"]
    model = Counting(llm.Client(store_mod.Store(config.DB_PATH)))
    failures, history = 0, ()
    for q in QUESTIONS:
        before = model.calls
        a = coach.ask(s, model, tid, q, history)
        ctx = coach._json(coach._song_slice(rec.doc, qa.route(q, history)))
        problems = qa.check(a, ctx, " ".join(t for _, t in history))
        failures += bool(problems)
        print(f"\nQ: {q}\n   topics={sorted(qa.route(q, history))} calls={model.calls - before} problems={problems}\nA: {a}")
        history += (("user", q), ("assistant", a))
    print(f"\n{len(QUESTIONS)} questions, {model.calls} model calls, {failures} answers with remaining problems")
