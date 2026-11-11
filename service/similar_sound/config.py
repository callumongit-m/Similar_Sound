"""Paths and version pins. Versions are stored in every record so stale data is detectable."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SIMILAR_SOUND_DATA", Path.home() / ".similar_sound"))
DB_PATH = DATA_DIR / "library.db"
LOG_PATH = DATA_DIR / "service.log"
TMP_DIR = DATA_DIR / "tmp"

SCHEMA_VERSION = 2   # 2: adds doc["loops"] (what each layer plays, bar by bar)
DEMUCS_MODEL = "htdemucs"
CLAP_MODEL = "laion/clap-htsat-unfused"

# Window around the drop: bars of build before, bars of drop after.
BUILD_BARS = 16
DROP_BARS = 16
ANALYSIS_SR = 22050
STEM_SR = 44100

API_PORT = 8765


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
