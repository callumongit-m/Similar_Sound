"""Ask any company's model, built so it cannot overspend and fails in words a producer understands.

Only text derived from the JSON ever leaves the machine, never audio. Spend is bounded three ways: the
cheapest suitable model per provider (providers.py), hard caps on input and output size, and a daily call
limit. Every failure becomes a short sentence the window can show as is; running out of credits is always
exactly the same sentence, whichever company the key belongs to.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from . import keychain
from .providers import PROVIDERS, Provider, detect

MAX_OUTPUT_TOKENS = 700
MAX_INPUT_CHARS = 24_000     # about 6k tokens: a track's whole analysis fits well inside this
DAILY_CALL_LIMIT = 150
OUT_OF_CREDITS = "I need more token credits"

_KEY_SHAPE = re.compile(r"[A-Za-z0-9_\-\.]{16,400}")
_MODEL_SHAPE = re.compile(r"[A-Za-z0-9_\-\.:/]{1,120}")


class LLMError(Exception):
    """Carries the sentence to show the user, and a code the window can react to."""
    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class OutOfCredits(LLMError):
    def __init__(self) -> None:
        super().__init__(OUT_OF_CREDITS, "credits")


class NoKey(LLMError):
    def __init__(self) -> None:
        super().__init__("I need an API key first. Use the key icon at the top left.", "no_key")


@dataclass(frozen=True)
class Creds:
    provider: str
    key: str
    model: str
    base_url: str | None = None      # only for "custom"


class LLM(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


def stored_creds() -> Creds | None:
    d = keychain.get()
    try:
        return Creds(**d) if d else None
    except TypeError:
        return None


def _base(c: Creds) -> str:
    return (c.base_url or PROVIDERS[c.provider].base_url or "").rstrip("/")


# ---------------------------------------------------------------- wire formats
def _request(c: Creds, system: str, prompt: str) -> urllib.request.Request:
    kind, base, h = PROVIDERS[c.provider].kind, _base(c), {"Content-Type": "application/json"}
    if kind == "gemini":
        h["x-goog-api-key"] = c.key
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS, "temperature": 0.3}}
        return urllib.request.Request(f"{base}/models/{c.model}:generateContent", json.dumps(body).encode(), h)
    if kind == "anthropic":
        h.update({"x-api-key": c.key, "anthropic-version": "2023-06-01"})
        body = {"model": c.model, "max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0.3, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
        return urllib.request.Request(f"{base}/messages", json.dumps(body).encode(), h)
    h["Authorization"] = f"Bearer {c.key}"
    body = {"model": c.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if c.provider == "openai":   # OpenAI's newer models reject max_tokens, and reasoning models reject temperature
        body["max_completion_tokens"] = MAX_OUTPUT_TOKENS
        if not c.model.startswith(("gpt-5", "o1", "o3", "o4")):
            body["temperature"] = 0.3
    else:
        body["max_tokens"] = MAX_OUTPUT_TOKENS
        body["temperature"] = 0.3
    return urllib.request.Request(f"{base}/chat/completions", json.dumps(body).encode(), h)


def _answer(c: Creds, data: dict) -> str:
    kind = PROVIDERS[c.provider].kind
    if kind == "gemini":
        return data["candidates"][0]["content"]["parts"][0]["text"]
    if kind == "anthropic":
        return next(b["text"] for b in data["content"] if b.get("type") == "text")
    text = data["choices"][0]["message"]["content"]
    if not text:
        raise KeyError("empty")
    return text


def _list_request(p: Provider, key: str, base: str) -> urllib.request.Request:
    if p.kind == "gemini":
        return urllib.request.Request(f"{base}/models?pageSize=200", headers={"x-goog-api-key": key})
    if p.kind == "anthropic":
        return urllib.request.Request(f"{base}/models?limit=100", headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    return urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})


def _model_names(p: Provider, data: dict) -> set[str]:
    if p.kind == "gemini":
        return {m["name"].removeprefix("models/") for m in data.get("models", [])}
    return {m["id"] for m in data.get("data", [])}


# ---------------------------------------------------------------- errors
def _translate(code: int, raw: str) -> LLMError:
    """Map any company's error response onto sentences. Quota, billing and credit problems are all
    'out of credits'; an explicit rate limit is its own message; everything else is plain."""
    try:
        body = json.loads(raw or "{}")
    except ValueError:
        body = {}
    err = body.get("error", body) if isinstance(body, dict) else {}
    err = err if isinstance(err, dict) else {"message": str(err)}
    text = " ".join(str(err.get(k, "")) for k in ("status", "type", "code", "message")).lower()
    credit_words = ("quota", "billing", "credit", "insufficient", "balance", "exceeded your current", "resource_exhausted")
    if code == 402 or any(w in text for w in credit_words):
        return OutOfCredits()
    if code == 429:
        if any(w in text for w in ("rate limit", "rate_limit", "per minute", "too many requests", "overloaded")):
            return LLMError("I'm being rate limited. Try again in a minute.", "rate_limit")
        return OutOfCredits()
    if code in (401, 403) or "api key" in text or "api_key" in text or "authentication" in text:
        return LLMError("That key wasn't accepted. Add it again with the key icon.", "bad_key")
    if code == 404:
        return LLMError("That model isn't available to this key.", "bad_model")
    return LLMError("The model had a problem. Try again in a moment.")


def _call(opener: Callable, req: urllib.request.Request, timeout: int = 60) -> dict:
    try:
        with opener(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise _translate(e.code, e.read().decode(errors="replace")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LLMError("I can't reach the model right now. Check your connection.") from None


# ---------------------------------------------------------------- the client
class Client:
    def __init__(self, store=None, creds: Callable[[], Creds | None] = stored_creds, opener: Callable = urllib.request.urlopen):
        self._store, self._creds, self._open = store, creds, opener

    def _count_call(self) -> None:
        if self._store is None:
            return
        day = time.strftime("%Y-%m-%d")
        rows = self._store.execute("SELECT calls FROM usage WHERE day=?", (day,))
        if (rows[0]["calls"] if rows else 0) >= DAILY_CALL_LIMIT:
            raise LLMError(f"That's the daily limit of {DAILY_CALL_LIMIT} questions. It resets tomorrow.", "daily_limit")
        self._store.execute("INSERT INTO usage(day,calls) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1", (day,))

    def complete(self, system: str, prompt: str) -> str:
        c = self._creds()
        if not c:
            raise NoKey()
        self._count_call()
        data = _call(self._open, _request(c, system, prompt[:MAX_INPUT_CHARS]))
        try:
            return _answer(c, data)
        except (KeyError, IndexError, StopIteration):
            raise LLMError("The model didn't give an answer to that. Try rephrasing.") from None


# ---------------------------------------------------------------- connecting a key
def providers_for_ui() -> list[dict]:
    return [{"id": p.id, "name": p.name, "needs_address": p.id == "custom"} for p in PROVIDERS.values()]


def connect(key: str, provider: str | None = None, base_url: str | None = None, model: str | None = None,
            opener: Callable = urllib.request.urlopen) -> Creds:
    """Work out which company a key is for, prove it works with a free call, and choose the cheapest usable
    model. Nothing is spent. Raises LLMError with a sentence (and code 'unknown_provider' / 'need_model' when the
    window should ask the user one more thing)."""
    key = (key or "").strip()
    if not _KEY_SHAPE.fullmatch(key):
        raise LLMError("That doesn't look like an API key.", "bad_key")
    p = PROVIDERS.get(provider or "") or detect(key)
    if p is None:
        raise LLMError("I don't recognise that key's company. Pick it from the list.", "unknown_provider")
    if p.id == "custom":
        u = urllib.parse.urlsplit(base_url or "")
        if not (u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"))) or not u.netloc:
            raise LLMError("The address should start with https://", "need_address")
        base_url = (base_url or "").rstrip("/")
    else:
        base_url = None
    if model and not _MODEL_SHAPE.fullmatch(model):
        raise LLMError("That model name has odd characters in it.", "need_model")
    base = base_url or p.base_url or ""
    names = _model_names(p, _call(opener, _list_request(p, key, base), timeout=20))   # free; also proves the key works
    chosen = model or next((m for m in p.models if m in names), None)
    if chosen is None:
        raise LLMError(f"That key works, but I couldn't pick a cheap {p.name} model automatically. Type the model name.", "need_model")
    if p.id != "custom" and chosen not in names:
        raise LLMError(f"{chosen} isn't available to that key.", "bad_model")
    return Creds(p.id, key, chosen, base_url)


def save(creds: Creds) -> None:
    keychain.set(asdict(creds))
