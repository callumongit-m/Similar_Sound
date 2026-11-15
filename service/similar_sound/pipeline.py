"""ingest(): the one deep entry point. source (URL or file) -> TrackRecord (doc + vectors).

Stages are sequentially dependent (embeddings are per stem), so they run in order; the caller
gets `checkpoint()` between stages so the queue can hold work while Ableton is open.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit
from dataclasses import asdict, dataclass
from typing import Callable

import librosa
import numpy as np

from . import config, download, features, models, segment


@dataclass
class TrackRecord:
    doc: dict
    vectors: dict[str, np.ndarray]


def track_id(source: str) -> str:
    """Stable id for a track. SoundCloud's share button appends ?si=...&utm_... and a `#t=` override
    pins a drop: none of those change which song it is, so none may create a second library entry."""
    base = source.partition("#")[0].strip()
    parts = urlsplit(base)
    if parts.netloc.lower().endswith("soundcloud.com"):  # on soundcloud.com the path is the identity
        base = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    return hashlib.sha1(base.encode()).hexdigest()[:12]


def _load_stereo(path, sr: int) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=sr, mono=False)
    return y if y.ndim == 2 else np.stack([y, y])


def ingest(source: str, checkpoint: Callable[[], None] = lambda: None,
           progress: Callable[[str], None] = lambda s: None) -> TrackRecord:
    """Every decision the segmenter makes is kept in doc["detection"], so a wrong drop is diagnosable."""
    progress("download")
    fetched = download.fetch(source)
    try:
        checkpoint()
        progress("segment")
        analysis = librosa.load(str(fetched.path), sr=config.ANALYSIS_SR, mono=True)[0]
        detection: dict = {}
        length = len(analysis) / config.ANALYSIS_SR
        if fetched.drop_start_override is not None and fetched.drop_start_override >= length - 1:
            m, sec = divmod(int(length), 60)
            raise ValueError(f"That timestamp is past the end of the audio, which is only {m}:{sec:02d} long. "
                             "Check that this is the full track and not a preview.")
        seg = segment.find_segments(analysis, config.ANALYSIS_SR, fetched.drop_start_override, trace=detection)
        full = _load_stereo(fetched.path, config.STEM_SR)
        t0, t1 = int(seg.build[0] * config.STEM_SR), int(seg.drop[1] * config.STEM_SR)
        clip = full[:, t0:t1]
    finally:
        download.cleanup(fetched)  # audio never outlives the job

    checkpoint()
    progress("stems")
    parts = models.split(clip)

    checkpoint()
    progress("features")
    split_s = seg.drop[0] - seg.build[0]
    doc = features.extract(clip, parts, seg.bpm, split_s)

    checkpoint()
    progress("embed")
    cut = int(split_s * config.STEM_SR)
    sr = config.STEM_SR
    vectors = {"mix": models.embed(clip[:, cut:], sr)}
    for name in ("drums", "bass", "other"):
        vectors[name] = models.embed(parts[name][:, cut:], sr)
    vectors["groove"] = np.array(doc["groove"]["vector"], dtype=np.float32)

    doc.update({
        "id": track_id(source), "source": fetched.source, "title": fetched.title, "artist": fetched.artist,
        "source_meta": asdict(fetched.meta),
        "segments": {"build": list(seg.build), "drop": list(seg.drop)},
        "detection_confidence": round(seg.confidence, 3),
        "alternative_drops": [round(t, 2) for t in seg.alternatives],
        "detection": detection,
        "versions": {"schema": config.SCHEMA_VERSION, "demucs": config.DEMUCS_MODEL, "clap": config.CLAP_MODEL},
    })
    return TrackRecord(doc, vectors)
