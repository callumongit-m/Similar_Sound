"""The API key (and which company/model it is for) lives in the macOS Keychain, never in a file, the repo,
a log or the page.

The value is base64 of a small JSON document, so nothing in it can break the command line, and it is fed
to the `security` tool through stdin (`security -i`) so it never appears in a process argument list.
Reading goes the other way, into memory only, for the moment a request is made.
"""
from __future__ import annotations

import base64
import json
import subprocess

SERVICE = "similar-sound-llm"
ACCOUNT = "similar-sound"


def get() -> dict | None:
    r = subprocess.run(["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(base64.b64decode(r.stdout.strip()))
    except (ValueError, TypeError):
        return None


def exists() -> bool:
    return get() is not None


def set(creds: dict) -> None:
    blob = base64.b64encode(json.dumps(creds).encode()).decode()   # [A-Za-z0-9+/=] only
    r = subprocess.run(["security", "-i"], input=f'add-generic-password -U -a {ACCOUNT} -s {SERVICE} -w "{blob}"\n',
                       capture_output=True, text=True)
    if r.returncode != 0 or get() != creds:
        raise RuntimeError("Couldn't save the key to the Keychain.")


def delete() -> None:
    subprocess.run(["security", "delete-generic-password", "-a", ACCOUNT, "-s", SERVICE], capture_output=True)
