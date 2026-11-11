"""Analyse audio captured from the user's project: the same measurements as a library song, minus the
hunt for a drop (they played the section they care about) and minus embeddings (nothing to search).
The audio is deleted by the caller; only this document is kept."""
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from . import config, features, segment, stems

SILENCE = 10 ** (-50 / 20)   # -50 dBFS: below this is not music
MIN_SECONDS = 4.0
MAX_SECONDS = 90.0


class ProjectError(Exception):
    """Carries the sentence to show the user."""


def analyse(wav: Path) -> dict:
    y, _ = librosa.load(str(wav), sr=config.STEM_SR, mono=False)
    y = y if y.ndim == 2 else np.stack([y, y])
    loud = np.flatnonzero(np.abs(y).max(axis=0) > SILENCE)
    if loud.size == 0:
        raise ProjectError("I didn't hear anything. Press play in Ableton and try again.")
    y = y[:, loud[0]:loud[-1] + 1]                                  # trim the silence either side
    if y.shape[1] < MIN_SECONDS * config.STEM_SR:
        raise ProjectError("I only heard a moment of audio. Let it play for at least a few seconds.")
    y = y[:, :int(MAX_SECONDS * config.STEM_SR)]
    try:
        bpm = segment.estimate_bpm(librosa.resample(y.mean(0), orig_sr=config.STEM_SR, target_sr=config.ANALYSIS_SR),
                                   config.ANALYSIS_SR)
    except ValueError:
        raise ProjectError("I couldn't find a steady tempo in that. Play a section with the drums in.") from None
    parts = stems.split(y)
    doc = features.extract(y, parts, bpm, None)
    doc.update(kind="project", title="Your project", artist="", seconds=round(y.shape[1] / config.STEM_SR, 1))
    return doc
