import io
import json
import urllib.error

import pytest

from similar_sound import llm
from similar_sound.providers import PROVIDERS, detect
from similar_sound.store import Store


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


def ok(payload):
    return lambda req, timeout=0: _Resp(json.dumps(payload).encode())


def http(code, payload=None):
    def opener(req, timeout=0):
        raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps(payload or {}).encode()))
    return opener


def recording(payload, seen):
    def opener(req, timeout=0):
        seen["url"], seen["headers"], seen["body"] = req.full_url, {k.lower(): v for k, v in req.header_items()}, json.loads(req.data or b"{}")
        return _Resp(json.dumps(payload).encode())
    return opener


KEY = "k" * 32
GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
ANTHROPIC_OK = {"content": [{"type": "text", "text": "hi"}]}
OPENAI_OK = {"choices": [{"message": {"content": "hi"}}]}


def client(tmp_path, opener, provider="gemini", model=None):
    c = llm.Creds(provider, KEY, model or PROVIDERS[provider].models[0], "https://example.com/v1" if provider == "custom" else None)
    return llm.Client(Store(tmp_path / "g.db"), creds=lambda: c, opener=opener)


# ---------- which company is a key from ----------
@pytest.mark.parametrize("key,expected", [
    ("AIzaSyAAAAAAAAAAAAAAAAAAAAAA", "gemini"), ("sk-ant-api03-AAAAAAAAAAAA", "anthropic"), ("sk-proj-AAAAAAAAAAAAAAAA", "openai"),
    ("sk-AAAAAAAAAAAAAAAAAAAAAAAA", "openai"), ("sk-or-v1-AAAAAAAAAAAAAAAA", "openrouter"), ("gsk_AAAAAAAAAAAAAAAAAAAA", "groq"),
    ("xai-AAAAAAAAAAAAAAAAAAAAAA", "xai"), ("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", None)])
def test_prefix_identifies_the_company(key, expected):
    p = detect(key)
    assert (p.id if p else None) == expected      # sk-ant- beats sk-, sk-or- beats sk-


# ---------- each company's real request shape ----------
def test_gemini_request(tmp_path):
    seen = {}
    client(tmp_path, recording(GEMINI_OK, seen)).complete("sys", "hello")
    assert seen["url"].endswith("/models/gemini-2.5-flash-lite:generateContent")
    assert seen["headers"]["x-goog-api-key"] == KEY and KEY not in json.dumps(seen["body"])
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == llm.MAX_OUTPUT_TOKENS


def test_anthropic_request(tmp_path):
    seen = {}
    assert client(tmp_path, recording(ANTHROPIC_OK, seen), "anthropic").complete("sys", "hello") == "hi"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == KEY and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["model"] == "claude-haiku-5-5" and seen["body"]["system"] == "sys"
    assert seen["body"]["max_tokens"] == llm.MAX_OUTPUT_TOKENS and KEY not in json.dumps(seen["body"])


def test_openai_request_uses_the_new_token_parameter_and_skips_temperature_for_reasoning_models(tmp_path):
    seen = {}
    assert client(tmp_path, recording(OPENAI_OK, seen), "openai", "gpt-4.1-nano").complete("sys", "hello") == "hi"
    assert seen["url"] == "https://api.openai.com/v1/chat/completions" and seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert seen["body"]["max_completion_tokens"] == llm.MAX_OUTPUT_TOKENS and "max_tokens" not in seen["body"]
    assert seen["body"]["temperature"] == 0.3
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    client(tmp_path, recording(OPENAI_OK, seen), "openai", "gpt-5-nano").complete("sys", "hello")
    assert "temperature" not in seen["body"]            # reasoning models reject it


def test_openai_compatible_providers_use_max_tokens_and_their_own_address(tmp_path):
    seen = {}
    client(tmp_path, recording(OPENAI_OK, seen), "groq").complete("s", "p")
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions" and seen["body"]["max_tokens"] == llm.MAX_OUTPUT_TOKENS
    client(tmp_path, recording(OPENAI_OK, seen), "custom", "some-model").complete("s", "p")
    assert seen["url"] == "https://example.com/v1/chat/completions"


def test_input_is_capped_for_every_company(tmp_path):
    for provider, payload in (("gemini", GEMINI_OK), ("anthropic", ANTHROPIC_OK), ("openai", OPENAI_OK)):
        seen = {}
        client(tmp_path, recording(payload, seen), provider).complete("s", "Q" * 100_000)
        body = json.dumps(seen["body"])
        assert body.count("Q") == llm.MAX_INPUT_CHARS, provider


def test_daily_call_limit_stops_spend(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DAILY_CALL_LIMIT", 3)
    c = client(tmp_path, ok(GEMINI_OK))
    for _ in range(3):
        c.complete("s", "p")
    with pytest.raises(llm.LLMError, match="daily limit"):
        c.complete("s", "p")


def test_empty_or_odd_responses_become_a_sentence(tmp_path):
    with pytest.raises(llm.LLMError, match="didn't give an answer"):
        client(tmp_path, ok({"choices": [{"message": {"content": ""}}]}), "openai").complete("s", "p")
    with pytest.raises(llm.LLMError, match="didn't give an answer"):
        client(tmp_path, ok({"weird": 1}), "gemini").complete("s", "p")


# ---------- errors: every company's shape, one sentence for credits ----------
CREDIT_CASES = [
    ("gemini", 429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for metric"}}),
    ("gemini", 403, {"error": {"message": "billing account disabled"}}),
    ("gemini", 429, {}),
    ("openai", 429, {"error": {"type": "insufficient_quota", "code": "insufficient_quota", "message": "You exceeded your current quota, please check your plan and billing details."}}),
    ("anthropic", 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}}),
    ("openrouter", 402, {"error": {"message": "Insufficient credits"}}),
    ("groq", 429, {"error": {"message": "Quota exceeded"}}),
]


@pytest.mark.parametrize("provider,code,payload", CREDIT_CASES)
def test_out_of_credits_is_always_the_same_sentence(tmp_path, provider, code, payload):
    with pytest.raises(llm.LLMError) as e:
        client(tmp_path, http(code, payload), provider).complete("s", "p")
    assert str(e.value) == "I need more token credits" and e.value.code == "credits"


def test_a_plain_rate_limit_is_not_reported_as_no_credits(tmp_path):
    with pytest.raises(llm.LLMError) as e:
        client(tmp_path, http(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "Number of requests per minute exceeded"}}), "anthropic").complete("s", "p")
    assert e.value.code == "rate_limit" and "rate limited" in str(e.value)


def test_other_failures_get_their_own_plain_sentences(tmp_path):
    for provider in ("gemini", "anthropic", "openai"):
        with pytest.raises(llm.LLMError) as e:
            client(tmp_path, http(401, {"error": {"message": "Incorrect API key provided"}}), provider).complete("s", "p")
        assert e.value.code == "bad_key" and "wasn't accepted" in str(e.value)
    with pytest.raises(llm.LLMError, match="can't reach"):
        client(tmp_path, lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.URLError("down"))).complete("s", "p")
    with pytest.raises(llm.NoKey) as e:
        llm.Client(Store(tmp_path / "n.db"), creds=lambda: None).complete("s", "p")
    assert e.value.code == "no_key"


# ---------- connecting a key: detect, prove it works for free, pick the cheapest model it can use ----------
def models(provider, names):
    if PROVIDERS[provider].kind == "gemini":
        return ok({"models": [{"name": f"models/{n}"} for n in names]})
    return ok({"data": [{"id": n} for n in names]})


def test_connect_picks_the_cheapest_candidate_the_key_can_use():
    c = llm.connect("sk-proj-" + "a" * 30, opener=models("openai", ["gpt-5-nano", "gpt-4o-mini", "gpt-5"]))
    assert (c.provider, c.model) == ("openai", "gpt-5-nano")                # gpt-4.1-nano isn't available, next cheapest is
    c = llm.connect("sk-proj-" + "a" * 30, opener=models("openai", ["gpt-4.1-nano", "gpt-5-nano"]))
    assert c.model == "gpt-4.1-nano"
    c = llm.connect("sk-ant-" + "a" * 30, opener=models("anthropic", ["claude-haiku-5-5", "claude-opus-5-5"]))
    assert (c.provider, c.model) == ("anthropic", "claude-haiku-5-5")
    c = llm.connect("AIza" + "a" * 35, opener=models("gemini", ["gemini-2.5-flash-lite", "gemini-2.5-pro"]))
    assert (c.provider, c.model) == ("gemini", "gemini-2.5-flash-lite")


def test_connect_does_not_spend_anything_it_only_lists_models():
    seen = {}
    llm.connect("AIza" + "a" * 35, opener=recording({"models": [{"name": "models/gemini-2.5-flash-lite"}]}, seen))
    assert seen["url"].endswith("/models?pageSize=200") and seen["body"] == {}        # a GET, no generation


def test_unknown_company_asks_which_one_then_works():
    with pytest.raises(llm.LLMError) as e:
        llm.connect("a" * 40, opener=models("mistral", []))
    assert e.value.code == "unknown_provider"
    c = llm.connect("a" * 40, provider="mistral", opener=models("mistral", ["mistral-small-latest"]))
    assert (c.provider, c.model) == ("mistral", "mistral-small-latest")


def test_custom_company_needs_an_https_address_and_a_model_name():
    with pytest.raises(llm.LLMError) as e:
        llm.connect("a" * 40, provider="custom", base_url="http://evil.example.com/v1", model="m", opener=models("openai", ["m"]))
    assert e.value.code == "need_address"
    c = llm.connect("a" * 40, provider="custom", base_url="https://api.example.com/v1/", model="m", opener=models("openai", []))
    assert (c.base_url, c.model) == ("https://api.example.com/v1", "m")
    c = llm.connect("a" * 40, provider="custom", base_url="http://localhost:11434/v1", model="llama3", opener=models("openai", []))
    assert c.base_url == "http://localhost:11434/v1"                              # a local model is fine over plain http


def test_when_no_cheap_model_is_found_it_asks_instead_of_guessing():
    with pytest.raises(llm.LLMError) as e:
        llm.connect("sk-proj-" + "a" * 30, opener=models("openai", ["some-huge-model"]))
    assert e.value.code == "need_model"


def test_bad_keys_are_rejected_before_any_network_call_or_command_line():
    for bad in ['abc"; rm -rf ~', "short", "has space " * 6, "quote\"" + "a" * 30, ""]:
        with pytest.raises(llm.LLMError) as e:
            llm.connect(bad, opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call out")))
        assert e.value.code == "bad_key"


def test_a_key_the_company_rejects_is_reported_not_saved():
    with pytest.raises(llm.LLMError) as e:
        llm.connect("AIza" + "a" * 35, opener=http(400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid. Please pass a valid API key."}}))
    assert e.value.code == "bad_key"
