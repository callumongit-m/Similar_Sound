"""Live spectrum of the Ableton mix: the "Mix" tab.

Interface: MixMeter().frames() yields one JSON-able dict about 30 times a second for as long as the caller
keeps iterating (the window's open stream). Closing the iterator stops the audio helper.
  {"state": "waiting", "message": ...}                      Ableton closed, or not playing yet
  {"state": "live", "bands": [dB x BANDS], "rms": dB}       bands run 20 Hz to 20 kHz, log-spaced
The audio comes from the same Ableton process tap as "Compare my project", streamed (tools/audiotap --stream).
It is analysed in memory and dropped: nothing is written to disk.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Callable, Iterator, Protocol

import numpy as np

from . import ableton, listen

log = logging.getLogger("similar_sound.mix")

BANDS = 96
FPS = 30
FFT = 4096
F_LO, F_HI = 20.0, 20000.0
FLOOR_DB = -120.0


class PcmSource(Protocol):
    rate: int
    channels: int
    def read(self, frames: int) -> np.ndarray | None: ...    # (frames, channels) float32, None at the end
    def close(self) -> None: ...


class HelperSource:
    """tools/audiotap --stream on Ableton's audio."""

    def __init__(self):
        if not listen.HELPER.exists():
            raise listen.TapError("I couldn't find my audio listener (run service/tools/build.sh).")
        pid = ableton.pid()
        if pid is None:
            raise listen.TapNotReady("Open Ableton Live and press play.")
        self._p = subprocess.Popen([str(listen.HELPER), "--pid", str(pid), "--stream"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        head = self._p.stdout.readline().decode(errors="ignore").split()
        if len(head) != 3 or head[0] != "FMT":
            code = self._p.wait()
            if code == 3:
                raise listen.TapError("macOS hasn't let me listen to Ableton yet. Allow Similar Sound under "
                                      "System Settings > Privacy & Security > Audio Recording.")
            raise listen.TapNotReady("Waiting for Ableton's audio. Press play.")
        self.rate, self.channels = int(head[1]), int(head[2])

    def read(self, frames: int) -> np.ndarray | None:
        n = frames * self.channels * 4
        raw = self._p.stdout.read(n)
        if len(raw) < n:
            return None
        return np.frombuffer(raw, dtype=np.float32).reshape(frames, self.channels)

    def close(self) -> None:
        if self._p.poll() is None:
            self._p.send_signal(2)
            try:
                self._p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._p.kill()


def band_edges() -> np.ndarray:
    return np.geomspace(F_LO, F_HI, BANDS + 1)


class Analyser:
    """Rolling window of the last FFT samples -> BANDS levels in dBFS (a full-scale sine reads 0)."""

    def __init__(self, rate: int):
        self.rate = rate
        self.buf = np.zeros(FFT, dtype=np.float32)
        self.win = np.hanning(FFT).astype(np.float32)
        self.gain = 2.0 / self.win.sum()
        freqs = np.fft.rfftfreq(FFT, 1.0 / rate)
        edges = band_edges()
        self.centres = np.sqrt(edges[:-1] * edges[1:])
        self.freqs = freqs
        # the bins inside each band; bands narrower than one bin fall back to interpolation between bins
        self.spans = [np.nonzero((freqs >= lo) & (freqs < hi))[0] for lo, hi in zip(edges[:-1], edges[1:])]

    def push(self, block: np.ndarray) -> None:
        mono = block.mean(axis=1) if block.ndim == 2 else block
        n = len(mono)
        self.buf = np.concatenate([self.buf, mono])[-FFT:] if n < FFT else mono[-FFT:].astype(np.float32)

    def bands(self) -> list[float]:
        mag = np.abs(np.fft.rfft(self.buf * self.win)) * self.gain
        db = 20 * np.log10(np.maximum(mag, 1e-6))
        out = np.interp(self.centres, self.freqs, db)
        for i, span in enumerate(self.spans):
            if len(span):
                out[i] = db[span].max()
        return [round(float(v), 1) for v in np.maximum(out, FLOOR_DB)]


def rms_db(block: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) if block.size else 0.0
    return round(20 * np.log10(max(r, 1e-6)), 1)


class MixMeter:
    def __init__(self, make_source: Callable[[], PcmSource] = HelperSource, retry_s: float = 1.0):
        self._make, self._retry = make_source, retry_s

    def frames(self) -> Iterator[dict]:
        while True:
            try:
                src = self._make()
            except listen.TapNotReady as e:
                yield {"state": "waiting", "message": str(e)}
                time.sleep(self._retry)
                continue
            except listen.TapError as e:
                log.warning("mix meter: %s", e)
                yield {"state": "error", "message": str(e)}
                return
            log.info("mix meter listening (%d Hz, %d ch)", src.rate, src.channels)
            an, hop = Analyser(src.rate), max(1, src.rate // FPS)
            try:
                while (block := src.read(hop)) is not None:
                    an.push(block)
                    yield {"state": "live", "bands": an.bands(), "rms": rms_db(block)}
            finally:
                src.close()
            yield {"state": "waiting", "message": "Ableton's audio stopped. Press play."}
            time.sleep(self._retry)
