"""Text-only LLM access (Gemini free tier). Only JSON-derived text ever leaves the machine, never audio.
Key: GEMINI_API_KEY env var or ~/.similar_sound/config.json {"gemini_api_key": "...", "gemini_model": "..."}.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol

from . import config


class LLM(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


class Gemini:
    def __init__(self):
        cfg_path = config.DATA_DIR / "config.json"
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        self.key = os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key")
        self.model = cfg.get("gemini_model", "gemini-2.5-flash")
        if not self.key:
            raise RuntimeError("No Gemini key: set GEMINI_API_KEY or ~/.similar_sound/config.json")

    def complete(self, system: str, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        req = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "x-goog-api-key": self.key})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        return data["candidates"][0]["content"]["parts"][0]["text"]
