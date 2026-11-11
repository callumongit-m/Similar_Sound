"""Which companies' API keys the app understands, and the cheapest model to use for each.

`models` lists candidates cheapest first. At save time the app asks the provider which models the key can
actually use (a free call) and takes the first candidate that exists, so a retired or renamed model never
silently picks something expensive. Prices move fast; to change a default, edit this table.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str                       # wire format: "gemini" | "anthropic" | "openai" (OpenAI-compatible)
    base_url: str | None
    prefixes: tuple[str, ...] = ()  # key prefixes that identify the company
    models: tuple[str, ...] = ()    # cheapest first


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta", ("AIza",),
             # 2.5 is cheaper but Google no longer lets new users call it even though it is still listed;
             # the app tries each candidate with a tiny real request, so this order is "cheapest that works".
             ("gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite")),
    Provider("anthropic", "Anthropic Claude", "anthropic", "https://api.anthropic.com/v1", ("sk-ant-",),
             ("claude-haiku-5-5", "claude-haiku-4-5-20251001")),
    Provider("openai", "OpenAI", "openai", "https://api.openai.com/v1", ("sk-",),
             ("gpt-4.1-nano", "gpt-5-nano", "gpt-4o-mini")),
    Provider("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", ("sk-or-",),
             ("google/gemini-2.5-flash-lite", "openai/gpt-4.1-nano")),
    Provider("groq", "Groq", "openai", "https://api.groq.com/openai/v1", ("gsk_",), ("llama-3.1-8b-instant",)),
    Provider("xai", "xAI Grok", "openai", "https://api.x.ai/v1", ("xai-",), ("grok-3-mini",)),
    Provider("mistral", "Mistral", "openai", "https://api.mistral.ai/v1", (), ("mistral-small-latest", "ministral-8b-latest")),
    Provider("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", (), ("deepseek-chat",)),
    # Any other company that speaks the OpenAI format: the user supplies the address and a model name.
    Provider("custom", "Other (OpenAI-compatible)", "openai", None),
)}


def detect(key: str) -> Provider | None:
    """The company a key belongs to, from its prefix (longest prefix wins: sk-ant- beats sk-). None if unsure."""
    best, best_len = None, 0
    for p in PROVIDERS.values():
        for pre in p.prefixes:
            if key.startswith(pre) and len(pre) > best_len:
                best, best_len = p, len(pre)
    return best
