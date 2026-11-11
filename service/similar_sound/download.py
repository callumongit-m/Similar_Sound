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

from . import config


@dataclass
class Fetched:
    path: Path
    title: str
    artist: str
    source: str
    drop_start_override: float | None


def split_title(title: str, uploader: str = "") -> tuple[str, str]:
    """(song, artist). SoundCloud titles are usually "Artist - Song"; the uploader is often a label or
    a promoter rather than the artist, so the title wins when it has that shape."""
    t = (title or "").strip()
    if " - " in t:
        artist, song = t.split(" - ", 1)
        return song.strip(), artist.strip()
    return t, (uploader or "").strip()


def peek(source: str) -> tuple[str, str] | None:
    """(song, artist) without downloading audio (about a second), so the UI can show the real name
    the moment a link is pasted. None if it cannot be read; the pipeline still fills it in later."""
    base = source.partition("#")[0]
    local = Path(base).expanduser()
    if local.exists():
        return local.stem, ""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(base, download=False)
    return split_title(info.get("title", ""), info.get("uploader", ""))


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
        return Fetched(dest, local.stem, "", base, override)

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
    song, artist = split_title(info.get("title", ""), info.get("uploader", ""))
    return Fetched(wav, song, artist, base, override)


def cleanup(fetched: Fetched) -> None:
    shutil.rmtree(fetched.path.parent, ignore_errors=True)
