"""CLAP embeddings (local, free). CLAP takes 10 s windows, so a segment is the mean of several."""
from __future__ import annotations

import librosa
import numpy as np
import torch

from . import config

CLAP_SR = 48000
WINDOW_S = 10
_model = _proc = None


def _load():
    global _model, _proc
    if _model is None:
        from transformers import ClapModel, ClapProcessor
        _model = ClapModel.from_pretrained(config.CLAP_MODEL).eval()
        _proc = ClapProcessor.from_pretrained(config.CLAP_MODEL)
    return _model, _proc


def embed(audio: np.ndarray, sr: int, max_windows: int = 6) -> np.ndarray:
    """audio: (channels, samples). Returns an L2-normalised vector."""
    model, proc = _load()
    mono = librosa.resample(audio.mean(0), orig_sr=sr, target_sr=CLAP_SR)
    win = WINDOW_S * CLAP_SR
    starts = list(range(0, max(len(mono) - win, 1), win // 2))[:max_windows] or [0]
    chunks = [mono[s:s + win] for s in starts]
    try:
        inputs = proc(audio=chunks, sampling_rate=CLAP_SR, return_tensors="pt")
    except TypeError:
        inputs = proc(audios=chunks, sampling_rate=CLAP_SR, return_tensors="pt")
    with torch.no_grad():
        v = model.get_audio_features(**inputs)
    v = getattr(v, "pooler_output", v)
    v = v.mean(0).numpy()
    return v / (np.linalg.norm(v) + 1e-12)
