"""Gemini access, built so it cannot overspend and fails in words a producer understands.

Only text derived from the JSON ever leaves the machine, never audio. Spend is bounded three ways: the
cheapest suitable model, hard caps on input and output size, and a daily call limit. Every failure becomes
a short sentence the window can show as is; running out of credits is always exactly the same sentence.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

from . import config, keychain

# gemini-2.5-flash-lite: the cheapest current Flash-Lite (about $0.10 / $0.40 per million tokens in/out).
# Override with {"gemini_model": "..."} in ~/.similar_sound/config.json if Google retires or reprices it.
DEFAULT_MODEL = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 700
MAX_INPUT_CHARS = 24_000     # about 6k tokens: a track's whole analysis fits well inside this
DAILY_CALL_LIMIT = 150
API = "https://generativelanguage.googleapis.com/v1beta"

OUT_OF_CREDITS = "I need more token credits"


class LLMError(Exception):
    """Carries the sentence to show the user."""


class OutOfCredits(LLMError):
    def __init__(self) -> None:
        super().__init__(OUT_OF_CREDITS)


class NoKey(LLMError):
    def __init__(self) -> None:
        super().__init__("I need your Gemini key first.")


class LLM(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


def _model() -> str:
    path = config.DATA_DIR / "config.json"
    try:
        return json.loads(path.read_text()).get("gemini_model", DEFAULT_MODEL) if path.exists() else DEFAULT_MODEL
    except (OSError, ValueError):
        return DEFAULT_MODEL


def _translate(err: urllib.error.HTTPError) -> LLMError:
    """Map Google's error responses onto sentences. Quota, billing and rate limits are all 'out of credits'."""
    try:
        body = json.loads(err.read().decode() or "{}").get("error", {})
    except ValueError:
        body = {}
    text = f'{body.get("status", "")} {body.get("message", "")}'.lower()
    if err.code == 429 or "resource_exhausted" in text or "quota" in text or "billing" in text or "credit" in text:
        return OutOfCredits()
    if err.code in (400, 401, 403) and ("api key" in text or "api_key" in text or "permission" in text or err.code in (401, 403)):
        return LLMError("Gemini didn't accept the key. Add it again.")
    if err.code == 404:
        return LLMError("That Gemini model isn't available to this key.")
    return LLMError("Gemini had a problem. Try again in a moment.")


class Gemini:
    def __init__(self, store=None, key: Callable[[], str | None] = keychain.get,
                 opener: Callable = urllib.request.urlopen):
        self._store, self._key, self._open = store, key, opener

    # --- spend guard -------------------------------------------------------------------------
    def _count_call(self) -> None:
        if self._store is None:
            return
        day = time.strftime("%Y-%m-%d")
        rows = self._store.execute("SELECT calls FROM usage WHERE day=?", (day,))
        calls = rows[0]["calls"] if rows else 0
        if calls >= DAILY_CALL_LIMIT:
            raise LLMError(f"That's the daily limit of {DAILY_CALL_LIMIT} questions. It resets tomorrow.")
        self._store.execute("INSERT INTO usage(day,calls) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1", (day,))

    def complete(self, system: str, prompt: str) -> str:
        key = self._key()
        if not key:
            raise NoKey()
        self._count_call()
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt[:MAX_INPUT_CHARS]}]}],
                "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS, "temperature": 0.3}}
        req = urllib.request.Request(f"{API}/models/{_model()}:generateContent", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with self._open(req, timeout=60) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise _translate(e) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise LLMError("I can't reach Gemini right now. Check your connection.") from None
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise LLMError("Gemini didn't give an answer to that. Try rephrasing.") from None


def check_key(key: str, opener: Callable = urllib.request.urlopen) -> None:
    """Validate a key without spending anything (listing models is free) and confirm the model exists."""
    req = urllib.request.Request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": key})
    try:
        with opener(req, timeout=20) as r:
            names = {m["name"].removeprefix("models/") for m in json.load(r).get("models", [])}
    except urllib.error.HTTPError as e:
        raise _translate(e) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LLMError("I can't reach Gemini right now. Check your connection.") from None
    if _model() not in names:
        raise LLMError(f"That key works, but {_model()} isn't available to it.")
