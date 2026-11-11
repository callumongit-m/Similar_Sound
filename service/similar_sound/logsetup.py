"""The service log: ~/.similar_sound/service.log.

It records what you would want to read when something goes wrong or when you wonder what the app did: startup,
each song's start / finish / failure (with the reason), the queue pausing for Ableton, listening and its result,
the analysis models loading and unloading, language-model failures, and any error. It does not record the window's
status polling (several lines a second), which is what made the old log 1 MB of noise.

The file rotates by itself: past MAX_BYTES it becomes service.log.1, the older ones shift along, and anything
beyond KEEP old files is deleted, so it can never grow past roughly (KEEP + 1) * MAX_BYTES.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

MAX_BYTES = 2_000_000
KEEP = 2
_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"

log = logging.getLogger("similar_sound")


def setup(path: Path) -> None:
    """Send the app's log lines (and uvicorn's errors) to a rotating file. Safe to call more than once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT, "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    uv = logging.getLogger("uvicorn.error")             # server errors and tracebacks, not request lines
    if handler not in uv.handlers:
        uv.addHandler(handler)
