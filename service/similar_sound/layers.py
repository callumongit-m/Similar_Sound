"""Five layers from Demucs's four stems: drums, bass, vocals, leads, other.

Demucs cannot tell a lead from a pad: both land in its "other" stem. This splits that stem by how the sound behaves,
which is approximate and is described that way to the language model:
  leads = the rhythmic, note-by-note part of the synths (stabs, plucks, arps, lead lines): energy that changes quickly
          over time (the percussive side of harmonic/percussive separation).
  other = what remains: sustained pads, held chords, drones, FX beds.
Both are masks over the same spectrogram, so they add back up to the original "other" stem.
"""
from __future__ import annotations

import librosa
import numpy as np

N_FFT = 2048
HOP = 512
MARGIN = 2.0       # >1 keeps ambiguous sound out of both masks' extremes: leads are clearly rhythmic, pads clearly held


def split_other(other: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """other: (channels, samples). Returns (leads, rest), same shape."""
    n = other.shape[1]
    specs = [librosa.stft(ch, n_fft=N_FFT, hop_length=HOP) for ch in other]
    mono = np.abs(sum(specs) / len(specs))
    harmonic, percussive = librosa.decompose.hpss(mono, margin=MARGIN)
    lead_mask = librosa.util.softmask(percussive, harmonic, power=2, split_zeros=False)
    leads = np.stack([librosa.istft(s * lead_mask, hop_length=HOP, length=n) for s in specs])
    return leads.astype(other.dtype), (other - leads).astype(other.dtype)


def five(stems: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Demucs stems -> the layers the rest of the app talks about. Vocals are kept when Demucs gave them."""
    leads, rest = split_other(stems["other"])
    out = {"drums": stems["drums"], "bass": stems["bass"]}
    if "vocals" in stems:
        out["vocals"] = stems["vocals"]
    out["leads"], out["other"] = leads, rest
    return out
