"""Get audio onto disk from a SoundCloud URL (yt-dlp) or a local file (e.g. your own bounce).

The returned file is temporary: the caller deletes it once the clip has been cut.
`url#t=2:10` pins the drop start (seconds or m:ss) when auto-detection misses.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config, names


@dataclass
class Fetched:
    path: Path
    title: str
    artist: str
    source: str
    drop_start_override: float | None
    meta: names.Meta = names.Meta()      # the raw name fields, kept so names can be re-derived without the network


def split_title(title: str, uploader: str = "", artist: str = "") -> tuple[str, str]:
    """(song, artist) from the raw SoundCloud fields, with no library knowledge. See names.py for the rules."""
    return names.resolve(names.Meta(title, uploader, artist))


def _meta(info: dict) -> names.Meta:
    return names.Meta(info.get("title") or "", info.get("uploader") or "", info.get("artist") or "")


def peek_meta(source: str) -> names.Meta | None:
    """The raw name fields without downloading any audio (about a second). None if they cannot be read."""
    base = source.partition("#")[0]
    local = Path(base).expanduser()
    if local.exists():
        return names.Meta(local.stem)
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as ydl:
        return _meta(ydl.extract_info(base, download=False))


def peek(source: str, known: frozenset[str] = frozenset()) -> tuple[str, str] | None:
    """(song, artist) right away, so the window can show the real name the moment a link is pasted."""
    meta = peek_meta(source)
    return names.resolve(meta, known) if meta else None


def _parse_override(fragment: str) -> float | None:
    m = re.search(r"t=(?:(\d+):)?(\d+(?:\.\d+)?)$", fragment)
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + float(m.group(2))


def fetch(source: str) -> Fetched:
    config.ensure_dirs()
    base, _, fragment = source.partition("#")
    override = _parse_override(fragment)
    work = Path(tempfile.mkdtemp(dir=config.TMP_DIR))

    local = Path(base).expanduser()
    if local.exists():
        dest = work / local.name
        shutil.copy(local, dest)
        return Fetched(dest, local.stem, "", base, override, names.Meta(local.stem))

    import yt_dlp

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(work / "audio.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(base, download=True)
    wav = next(work.glob("audio.wav"))
    meta = _meta(info)
    song, artist = names.resolve(meta)
    return Fetched(wav, song, artist, base, override, meta)


def cleanup(fetched: Fetched) -> None:
    shutil.rmtree(fetched.path.parent, ignore_errors=True)
