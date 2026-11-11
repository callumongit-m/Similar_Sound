"""The process that holds the heavy models (Demucs and CLAP).

They live in their own process so that all of their memory (about 800 MB) can be handed back to the operating
system by simply ending that process. Freeing the models inside the service only returned a fifth of it: the
allocator keeps the rest. models.py starts this on demand and stops it when it is not needed.

Protocol over a pipe: the service sends ("split", clip) / ("embed", audio, sr) / ("quit",) and gets back
("ok", result) or ("error", message). One request at a time, so the models are never used concurrently.
"""
from __future__ import annotations

import os


def _real_handlers() -> dict:
    from . import embed, stems   # torch and transformers are imported here, in the worker only

    return {"split": stems.split, "embed": embed.embed, "pid": os.getpid}


def _fake_handlers() -> dict:
    """Stand-ins for tests: the same protocol and process lifecycle, none of the weight."""
    import time

    import numpy as np

    def slow(seconds: float) -> str:
        time.sleep(seconds)
        return "done"

    def boom() -> None:
        raise ValueError("the model fell over")

    return {
        "split": lambda clip: {"drums": clip, "bass": clip * 0.5, "other": clip * 0.25, "vocals": clip * 0.0},
        "embed": lambda audio, sr: np.ones(512, dtype=np.float32) / 22.6,
        "pid": os.getpid, "slow": slow, "boom": boom,
    }


def serve(conn, fake: bool = False) -> None:
    handlers = _fake_handlers() if fake else _real_handlers()
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):          # the service went away: so do we
            return
        if msg[0] == "quit":
            return
        try:
            conn.send(("ok", handlers[msg[0]](*msg[1:])))
        except Exception as e:  # noqa: BLE001 - reported to the caller, which decides what to do
            conn.send(("error", f"{type(e).__name__}: {e}"))
