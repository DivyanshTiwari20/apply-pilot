"""Tests for the free-tier budget/pacing/quota logic in applypilot.llm.

All tests are offline — no real API calls. They cover the behaviour that makes
the free tier reliable: distinguishing daily quota from the per-minute limit,
stopping cleanly when the budget/quota is gone, and proactive pacing.
"""

import time

import httpx
import pytest

from applypilot import llm
from applypilot.llm import (
    DEFAULT_GEMINI_MODEL,
    QuotaExhausted,
    _detect_provider,
    _enforce_budget_and_pace,
    _is_daily_quota,
    configure_budget,
    get_call_count,
    reset_run_counter,
)


@pytest.fixture(autouse=True)
def _reset_budget(monkeypatch):
    """Isolate global budget state and env between tests."""
    monkeypatch.delenv("LLM_MIN_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("LLM_MAX_CALLS_PER_RUN", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    configure_budget(min_interval=0.0, max_calls=0)
    reset_run_counter()
    yield
    configure_budget(min_interval=0.0, max_calls=0)
    reset_run_counter()


def _resp(status: int, body: str) -> httpx.Response:
    req = httpx.Request("POST", "https://example.test/chat")
    return httpx.Response(status_code=status, content=body.encode(), request=req)


# ── Daily vs per-minute classification ────────────────────────────────────

def test_is_daily_quota_detects_per_day():
    body = '{"error":{"status":"RESOURCE_EXHAUSTED",' \
           '"details":[{"violations":[{"quotaId":"GenerateRequestsPerDayPerProject-FreeTier"}]}]}}'
    assert _is_daily_quota(_resp(429, body)) is True


def test_is_daily_quota_ignores_per_minute():
    body = '{"error":{"status":"RESOURCE_EXHAUSTED",' \
           '"details":[{"violations":[{"quotaId":"GenerateRequestsPerMinutePerProject-FreeTier"}]}]}}'
    assert _is_daily_quota(_resp(429, body)) is False


# ── Per-run call budget ───────────────────────────────────────────────────

def test_budget_cap_raises_quota_exhausted():
    configure_budget(max_calls=2)
    _enforce_budget_and_pace()
    _enforce_budget_and_pace()
    with pytest.raises(QuotaExhausted) as exc:
        _enforce_budget_and_pace()
    assert exc.value.reason == "budget"
    assert get_call_count() == 2  # the rejected call is not counted


def test_call_counter_resets():
    _enforce_budget_and_pace()
    assert get_call_count() == 1
    reset_run_counter()
    assert get_call_count() == 0


def test_env_var_overrides_max_calls(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CALLS_PER_RUN", "1")
    _enforce_budget_and_pace()
    with pytest.raises(QuotaExhausted):
        _enforce_budget_and_pace()


def test_detect_provider_prefers_gemini_over_stale_local_url(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("LLM_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "nvidia-key")
    monkeypatch.setenv("LLM_MODEL", "google/gemma-4-31b-it")

    base_url, model, api_key = _detect_provider()

    assert base_url == llm.GEMINI_COMPAT_BASE
    assert model == DEFAULT_GEMINI_MODEL
    assert api_key == "gemini-key"


# ── Proactive pacing ──────────────────────────────────────────────────────

def test_pacing_enforces_minimum_interval():
    configure_budget(min_interval=0.15)
    _enforce_budget_and_pace()           # first call: no wait
    start = time.monotonic()
    _enforce_budget_and_pace()           # second call: must wait ~0.15s
    elapsed = time.monotonic() - start
    assert elapsed >= 0.13               # small tolerance for timer granularity


# ── chat(): daily quota stops immediately, no long retry loop ──────────────

def test_chat_raises_quota_exhausted_on_daily_without_retrying(monkeypatch):
    client = llm.LLMClient("https://generativelanguage.googleapis.com/v1beta/openai",
                           DEFAULT_GEMINI_MODEL, "key")

    body = '{"error":{"status":"RESOURCE_EXHAUSTED","details":' \
           '[{"violations":[{"quotaId":"GenerateRequestsPerDayPerProject-FreeTier"}]}]}}'

    def fake_compat(messages, temperature, max_tokens):
        raise httpx.HTTPStatusError("429", request=_resp(429, body).request,
                                    response=_resp(429, body))

    monkeypatch.setattr(client, "_chat_compat", fake_compat)
    # If it retried with backoff, this would sleep for many seconds; guard it.
    monkeypatch.setattr(llm.time, "sleep", lambda *_: (_ for _ in ()).throw(
        AssertionError("daily quota must not trigger a backoff sleep")))

    with pytest.raises(QuotaExhausted) as exc:
        client.chat([{"role": "user", "content": "hi"}])
    assert exc.value.reason == "daily"
