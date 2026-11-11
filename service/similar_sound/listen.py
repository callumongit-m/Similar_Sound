"""Hear Ableton: wait for audio, record while it plays, stop when it stops, analyse what was heard.

Interface: start() / cancel() / status(). status() drives the window's orb:
  waiting -> listening -> analysing -> ready   (or error)
The recording itself is a Tap (the Swift helper in production, a scripted fake in tests); this class owns
only the rules: what counts as playing, when it has stopped, what is too short to bother with.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Protocol

from . import ableton, config, project
from .store import Store

PLAYING = 0.004          # rms (about -48 dBFS) above which Ableton counts as playing
STOP_AFTER_QUIET_S = 2.0
MAX_S = project.MAX_SECONDS
MIN_S = 4.0
HELPER = Path(__file__).resolve().parent.parent / "tools" / "audiotap"
HELPER_SRC = HELPER.with_suffix(".swift")


class TapError(Exception):
    """Carries the sentence to show the user."""


class TapNotReady(TapError):
    """Not a failure: Ableton is open but its audio engine isn't registered yet. Keep waiting."""


class Tap(Protocol):
    wav: Path
    def levels(self) -> Iterator[tuple[float, float]]: ...   # (rms, seconds recorded so far) until it ends
    def stop(self) -> None: ...                              # finalise the wav


class AbletonTap:
    """Spawns tools/audiotap, which listens to Ableton's own audio output (a Core Audio process tap)."""

    def __init__(self, wav: Path):
        self.wav = wav
        if not HELPER.exists():
            r = subprocess.run(["swiftc", "-O", "-swift-version", "5", str(HELPER_SRC), "-o", str(HELPER)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise TapError("I couldn't build my audio listener (run service/tools/build.sh to see why).")
        pid = ableton.pid()
        if pid is None:
            raise TapNotReady("Open Ableton Live and I'll listen.")
        self._p = subprocess.Popen([str(HELPER), "--pid", str(pid), "--out", str(wav), "--max", str(MAX_S)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def levels(self) -> Iterator[tuple[float, float]]:
        for line in self._p.stdout:               # "L <rms> <seconds>"
            parts = line.split()
            if len(parts) == 3 and parts[0] == "L":
                yield float(parts[1]), float(parts[2])
        code = self._p.wait()
        report = (self._p.stderr.read() or "").strip()
        if report:
            print(f"audiotap (exit {code}): " + " | ".join(report.splitlines()[-4:]), flush=True)   # lands in ~/.similar_sound/service.log
        if code not in (0, -2, -15):              # not a normal stop
            err = report.splitlines()
            if code == 4:
                raise TapNotReady("Waiting for Ableton's audio. Press play.")
            if code == 3:
                raise TapError("macOS hasn't let me listen to Ableton yet. Allow Similar Sound under "
                               "System Settings > Privacy & Security > Audio Recording, then try again.")
            raise TapError((err[-1] if err else "I couldn't listen to Ableton.")[:160])

    def stop(self) -> None:
        if self._p.poll() is None:
            self._p.send_signal(2)                # SIGINT: the helper writes the wav and exits
            try:
                self._p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._p.kill()


class Listener:
    def __init__(self, store: Store, make_tap: Callable[[Path], Tap] = AbletonTap,
                 ableton_running: Callable[[], bool] = ableton.is_running,
                 analyse: Callable[[Path], dict] = project.analyse):
        self._store, self._make_tap, self._running, self._analyse = store, make_tap, ableton_running, analyse
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._reset()

    def _reset(self) -> None:
        self.state, self.level, self.seconds, self.message = "idle", 0.0, 0.0, ""
        self._heard_levels = False
        self.project_id: int | None = None
        self.project: dict | None = None
        self.vectors: dict = {}              # the project's vibe vectors, in memory only

    # --- public ---
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._cancel.clear()
            self._reset()
            self.state = "waiting"
            self._thread = threading.Thread(target=self._run, daemon=True, name="listener")
            self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        if self._thread:
            self._thread.join(timeout=8)
        with self._lock:
            self._reset()

    def load_file(self, path: Path) -> None:
        """Skip the listening: analyse an exported bounce instead (also how the tests drive it)."""
        self._cancel.clear()
        self._reset()
        self._finish(path)

    def status(self) -> dict:
        heard = ({"bpm": self.project["bpm"], "key": self.project.get("key"),
                  "key_alternatives": self.project.get("key_alternatives", []), "seconds": self.project.get("seconds")}
                 if self.project else None)
        return {"state": self.state, "level": round(self.level, 3), "seconds": round(self.seconds, 1),
                "message": self.message, "ableton_running": self._running(), "project_id": self.project_id,
                "heard": heard}

    # --- internals ---
    def _run(self) -> None:
        wav = Path(tempfile.mkdtemp()) / "heard.wav"
        try:
            while not self._cancel.is_set():
                if not self._running():
                    self.state, self.message, self.level = "waiting", "Open Ableton Live and I'll listen.", 0.0
                    self._cancel.wait(1.0)
                    continue
                self.message = ""
                try:
                    if self._listen_once(wav):
                        break
                except TapNotReady as e:
                    self.state, self.message, self.level = "waiting", str(e), 0.0
                    self._cancel.wait(1.0)
            else:
                return
            self._finish(wav)
        except (TapError, project.ProjectError) as e:
            self.state, self.message = "error", str(e)
            if self._heard_levels and isinstance(e, project.ProjectError) and str(e).startswith("I didn't hear anything"):
                self.message = "I could hear Ableton, but my recording came out empty. That's a bug on my side; the details are in ~/.similar_sound/service.log."
        except Exception:                                    # noqa: BLE001
            self.state, self.message = "error", "Something went wrong while listening. Try again."
        finally:
            try:
                keep = config.DATA_DIR / "keep_capture"      # opt-in, for debugging: `touch ~/.similar_sound/keep_capture`
                if keep.exists() and wav.exists():
                    shutil.copy(wav, config.DATA_DIR / "last_capture.wav")
                wav.unlink(missing_ok=True)                  # otherwise the audio is never kept
                wav.parent.rmdir()
            except OSError:
                pass

    def _listen_once(self, wav: Path) -> bool:
        """One recording. True if it captured enough music to analyse; False to keep waiting."""
        tap = self._make_tap(wav)
        heard_from, quiet_since, last = None, None, 0.0
        try:
            for rms, t in tap.levels():
                if self._cancel.is_set():
                    return False
                self.level, self.seconds, last = min(rms * 6, 1.0), t, t
                if rms >= PLAYING:
                    self._heard_levels = True
                    heard_from = t if heard_from is None else heard_from
                    quiet_since = None
                    self.state = "listening"
                elif heard_from is not None:
                    quiet_since = t if quiet_since is None else quiet_since
                    if t - quiet_since >= STOP_AFTER_QUIET_S:
                        break                                # playback stopped
                if t >= MAX_S:
                    break
        finally:
            tap.stop()
        self.level = 0.0
        if heard_from is None:
            return False
        if last - heard_from - STOP_AFTER_QUIET_S < MIN_S and last < MAX_S:   # a blip, not a section
            self.state, self.seconds = "waiting", 0.0
            return False
        return True

    def _finish(self, wav: Path) -> None:
        self.state = "analysing"
        result = self._analyse(wav)
        doc, self.vectors = result if isinstance(result, tuple) else (result, {})
        self._store.execute("INSERT INTO projects(created, doc) VALUES (?, ?)", (time.time(), json.dumps(doc)))
        self.project_id = self._store.execute("SELECT max(id) AS i FROM projects")[0]["i"]
        self._store.prune_projects()
        self.project, self.state, self.message = doc, "ready", ""
