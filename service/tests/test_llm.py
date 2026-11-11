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
    assert seen["url"].endswith(":generateContent") and "/models/gemini-" in seen["url"]
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


def test_connecting_costs_next_to_nothing_one_free_list_and_one_tiny_test_request():
    reqs = []
    def opener(req, timeout=0):
        reqs.append((req.get_method(), req.full_url, json.loads(req.data or b"{}")))
        if ":generateContent" in req.full_url:
            return _Resp(json.dumps(GEMINI_OK).encode())
        return _Resp(json.dumps({"models": [{"name": "models/gemini-2.5-flash-lite"}]}).encode())
    llm.connect("AIza" + "a" * 35, opener=opener)
    assert [r[0] for r in reqs] == ["GET", "POST"]                                    # list (free), then one probe
    assert reqs[1][2]["generationConfig"]["maxOutputTokens"] == 8                      # a few tokens at most
    assert "Reply with OK" in json.dumps(reqs[1][2])


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


# ---------- a model that is LISTED but not USABLE (Google withdrew gemini-2.5-flash-lite for new users) ----------
RETIRED = {"error": {"status": "NOT_FOUND", "message": "This model models/gemini-2.5-flash-lite is no longer available to new users."}}


def gemini_router(listed, usable, calls=None):
    """GET /models lists `listed`; generateContent only answers for models in `usable`, 404 otherwise."""
    def opener(req, timeout=0):
        model = req.full_url.split("/models/")[-1].split(":")[0] if ":generateContent" in req.full_url else None
        if calls is not None:
            calls.append(model or "LIST")
        if model is None:
            return _Resp(json.dumps({"models": [{"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]} for m in listed]}).encode())
        if model in usable:
            return _Resp(json.dumps(GEMINI_OK).encode())
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(json.dumps(RETIRED).encode()))
    return opener


def test_connect_skips_a_listed_model_that_cannot_actually_be_called():
    calls = []
    c = llm.connect("AIza" + "a" * 35, opener=gemini_router(["gemini-2.5-flash-lite", "gemini-3.1-flash-lite"], {"gemini-3.1-flash-lite"}, calls))
    assert c.model == "gemini-3.1-flash-lite"
    assert calls == ["LIST", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite"]       # tried the cheapest first, then moved on


def test_connect_uses_the_cheapest_model_when_it_really_works():
    calls = []
    c = llm.connect("AIza" + "a" * 35, opener=gemini_router(["gemini-2.5-flash-lite", "gemini-3.1-flash-lite"], {"gemini-2.5-flash-lite", "gemini-3.1-flash-lite"}, calls))
    assert c.model == "gemini-2.5-flash-lite" and calls == ["LIST", "gemini-2.5-flash-lite"]   # one tiny test call, nothing more


def test_connect_with_nothing_usable_asks_for_a_model_name():
    with pytest.raises(llm.LLMError) as e:
        llm.connect("AIza" + "a" * 35, opener=gemini_router(["gemini-2.5-flash-lite"], set()))
    assert e.value.code == "need_model" and "Type the model name" in str(e.value)


def test_connect_when_the_key_has_no_credits_still_accepts_it():
    def opener(req, timeout=0):
        if ":generateContent" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 429, "x", {}, io.BytesIO(b'{"error":{"status":"RESOURCE_EXHAUSTED","message":"Quota exceeded"}}'))
        return _Resp(json.dumps({"models": [{"name": "models/gemini-2.5-flash-lite"}]}).encode())
    assert llm.connect("AIza" + "a" * 35, opener=opener).model == "gemini-2.5-flash-lite"   # key is real; credits show up when asking


def test_models_that_cannot_generate_text_are_never_candidates():
    def opener(req, timeout=0):
        return _Resp(json.dumps({"models": [{"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["embedContent"]},
                                            {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]}]}).encode()) \
            if ":generateContent" not in req.full_url else _Resp(json.dumps(GEMINI_OK).encode())
    assert llm.connect("AIza" + "a" * 35, opener=opener).model == "gemini-3.1-flash-lite"


def test_a_saved_model_that_gets_retired_repairs_itself_on_the_next_question(tmp_path):
    calls, saved = [], []
    opener = gemini_router(["gemini-2.5-flash-lite", "gemini-3.1-flash-lite"], {"gemini-3.1-flash-lite"}, calls)
    stale = llm.Creds("gemini", "AIza" + "a" * 35, "gemini-2.5-flash-lite")                # what an older version saved
    c = llm.Client(Store(tmp_path / "h.db"), creds=lambda: stale, opener=opener, save=saved.append)
    assert c.complete("s", "p") == "hi"                                                    # the user just gets an answer
    assert [x.model for x in saved] == ["gemini-3.1-flash-lite"]                           # and the fix is remembered
    assert calls[0] == "gemini-2.5-flash-lite" and calls[-1] == "gemini-3.1-flash-lite"


def test_repair_is_attempted_once_and_failures_surface(tmp_path):
    saved = []
    stale = llm.Creds("gemini", "AIza" + "a" * 35, "gemini-2.5-flash-lite")
    c = llm.Client(Store(tmp_path / "h.db"), creds=lambda: stale, opener=gemini_router(["gemini-2.5-flash-lite"], set()), save=saved.append)
    with pytest.raises(llm.LLMError) as e:
        c.complete("s", "p")
    assert e.value.code == "need_model" and saved == []


def test_no_key_message_is_the_one_the_user_asked_for():
    with pytest.raises(llm.NoKey) as e:
        llm.Client(None, creds=lambda: None).complete("s", "p")
    assert str(e.value) == "You need to add your own AI API key using the key icon in the top left"
