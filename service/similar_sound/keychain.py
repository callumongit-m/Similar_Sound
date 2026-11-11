"""The Gemini API key lives in the macOS Keychain, never in a file, the repo, a log or the page.

set() feeds the `security` tool through stdin (`security -i`), so the key never appears in a process
argument list. Reading goes the other way, into memory only, for the moment a request is made.
"""
from __future__ import annotations

import re
import subprocess

SERVICE = "similar-sound-gemini"
ACCOUNT = "similar-sound"
_KEY_SHAPE = re.compile(r"[A-Za-z0-9_\-]{20,100}")  # also guarantees nothing can break the command line below


def get() -> str | None:
    r = subprocess.run(["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
                       capture_output=True, text=True)
    key = r.stdout.strip()
    return key if r.returncode == 0 and key else None


def exists() -> bool:
    return get() is not None


def set(key: str) -> None:
    key = key.strip()
    if not _KEY_SHAPE.fullmatch(key):
        raise ValueError("That doesn't look like a Gemini API key.")
    r = subprocess.run(["security", "-i"], input=f'add-generic-password -U -a {ACCOUNT} -s {SERVICE} -w "{key}"\n',
                       capture_output=True, text=True)
    if r.returncode != 0 or get() != key:
        raise RuntimeError("Couldn't save the key to the Keychain.")


def delete() -> None:
    subprocess.run(["security", "delete-generic-password", "-a", ACCOUNT, "-s", SERVICE], capture_output=True)
