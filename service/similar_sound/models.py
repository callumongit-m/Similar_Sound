"""Use the heavy models (stem splitting, vibe embeddings) without keeping them in memory.

Interface: split(clip), embed(audio, sr), unload(), loaded().

The models run in a helper process (modelworker.py) that is started on the first call and stopped by unload()
(when the window is hidden) or after IDLE_UNLOAD_S of not being used. Stopping the process gives all of its memory
back to the Mac, which freeing the models inside this process cannot do. The next analysis pays about 8 seconds
to load them again. Calls are made one at a time, so two analyses never fight over the same models.
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time

import numpy as np

from . import modelworker

log = logging.getLogger("similar_sound.models")

IDLE_UNLOAD_S = 600       # not used for this long: give the memory back
WATCH_EVERY_S = 30
IN_PROCESS = False        # run the models inside this process instead (tests, debugging)
FAKE = False              # tests: a worker with trivial handlers (same process lifecycle, no models)

_lock = threading.Lock()                         # held for the whole of every call, so unload() can tell "busy"
_worker: tuple | None = None                     # (process, pipe)
_last_used = 0.0
_watcher: threading.Thread | None = None


def _start_locked() -> None:
    global _worker, _watcher
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=modelworker.serve, args=(child, FAKE), daemon=True, name="model-worker")
    proc.start()
    child.close()
    _worker = (proc, parent)
    log.info("analysis models loading (worker pid %s)", proc.pid)
    if _watcher is None or not _watcher.is_alive():
        _watcher = threading.Thread(target=_watch, daemon=True, name="model-idle-watch")
        _watcher.start()


def _stop_locked() -> bool:
    global _worker
    if _worker is None:
        return False
    proc, conn = _worker
    _worker = None
    try:
        conn.send(("quit",))
    except (OSError, ValueError):
        pass
    log.info("analysis models unloaded (worker pid %s)", proc.pid)
    proc.join(5)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
    conn.close()
    return True


def _in_process(name: str, *args):
    from . import embed as embed_model, stems

    return {"split": stems.split, "embed": embed_model.embed}[name](*args)


def _call(name: str, *args):
    global _last_used
    with _lock:
        if IN_PROCESS:
            return _in_process(name, *args)
        for attempt in (1, 2):
            if _worker is None or not _worker[0].is_alive():
                _stop_locked()
                _start_locked()
            try:
                _worker[1].send((name, *args))
                status, payload = _worker[1].recv()
                break
            except (EOFError, BrokenPipeError, OSError):      # it died mid-call: start a fresh one, once
                log.warning("analysis worker died mid-call (attempt %d)", attempt)
                _stop_locked()
                if attempt == 2:
                    raise RuntimeError("The analysis engine stopped unexpectedly. Try again.") from None
        _last_used = time.monotonic()
    if status == "error":
        raise RuntimeError(payload)
    return payload


def split(clip: np.ndarray) -> dict[str, np.ndarray]:
    """Drums / bass / other (/ vocals) stems of a (channels, samples) clip at 44.1 kHz."""
    return _call("split", clip)


def embed(audio: np.ndarray, sr: int) -> np.ndarray:
    """L2-normalised CLAP vibe vector of (channels, samples) audio."""
    return _call("embed", audio, sr)


def loaded() -> bool:
    return _worker is not None and _worker[0].is_alive()


def worker_pid() -> int | None:
    return _worker[0].pid if loaded() else None


def unload() -> bool:
    """Give the models' memory back. False if an analysis is using them right now (nothing is interrupted)."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        return _stop_locked()
    finally:
        _lock.release()


def _watch() -> None:
    while True:
        time.sleep(WATCH_EVERY_S)
        if _worker is not None and time.monotonic() - _last_used > IDLE_UNLOAD_S:
            unload()
