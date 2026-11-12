"""Real-model check of the Ableton how-to path. Needs the key in the Keychain; run from service/:
PYTHONPATH=. uv run python evals/howto_real.py"""
from similar_sound import coach, config, howto, llm, qa, store as store_mod

QUESTIONS = [
    "how do I make sure my groove is fully intact?",
    "how do I make my bass mono in Ableton?",
    "how do I sidechain the bass to the kick?",
    "where is the groove pool and how do I use grooves on a clip?",
    "how do I make a lead sound like a Moog in Ableton?",      # nothing in the steps covers this: it should say it is not sure
]


class Counting:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def complete(self, system, prompt, history=()):
        self.calls += 1
        return self.inner.complete(system, prompt, history)


if __name__ == "__main__":
    s = store_mod.Store(config.DB_PATH)
    tid = s.list()[0]["id"]
    model = Counting(llm.Client(s))
    for q in QUESTIONS:
        n = model.calls
        a = coach.ask(s, model, tid, q)
        print(f"\nQ: {q}\n   steps={[c['id'] for c in howto.retrieve(q)]} calls={model.calls - n} "
              f"unverified={howto.unverified_terms(a, howto.retrieve(q))} problems={qa.check(a, '', '', howto.retrieve(q))}\nA: {a}")
