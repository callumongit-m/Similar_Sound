"""Demucs stem split on the already-cut clip. Heavy: callers run it one job at a time."""
from __future__ import annotations

import numpy as np
import torch

from . import config

_model = None


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _load():
    global _model
    if _model is None:
        from demucs.pretrained import get_model
        _model = get_model(config.DEMUCS_MODEL)
        _model.eval()
    return _model


def split(clip: np.ndarray) -> dict[str, np.ndarray]:
    """clip: (channels, samples) at STEM_SR. Returns stem name -> (channels, samples)."""
    from demucs.apply import apply_model

    model = _load()
    wav = torch.from_numpy(clip).float()
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)
    try:
        out = apply_model(model, wav[None], device=_device(), split=True, progress=False)[0]
    except Exception:  # MPS op gaps: fall back to CPU rather than fail the job
        out = apply_model(model, wav[None], device="cpu", split=True, progress=False)[0]
    out = out * (ref.std() + 1e-8) + ref.mean()
    return {name: out[i].numpy() for i, name in enumerate(model.sources)}
