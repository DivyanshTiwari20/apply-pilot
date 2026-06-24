"""
Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY  -> Google Gemini (default: gemini-3.1-flash-lite)
  OPENAI_API_KEY  -> OpenAI (default: gpt-4o-mini)
  LLM_URL         -> Local llama.cpp / Ollama compatible endpoint

LLM_MODEL env var overrides the model name for any provider.
LLM_PROVIDER can force "gemini", "openai", or "local".
"""

import logging
import os
import threading
import time

import httpx

from applypilot import events

log = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_LOCAL_MODEL = "local-model"
GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_model(model_override: str) -> str:
    """Return a Gemini API model, ignoring stale local/OpenAI model overrides."""
    if model_override.startswith("models/"):
        model_override = model_override.removeprefix("models/")
    if model_override.startswith("gemini-"):
        return model_override
    return DEFAULT_GEMINI_MODEL


class QuotaExhausted(Exception):
    """Raised when no further LLM work can succeed in this run.

    Two causes, both *not* recoverable by retrying within the same run:
      - ``reason="daily"``: the provider's daily quota is gone (Gemini free tier
        returns HTTP 429 with a ``PerDay`` quota violation).
      - ``reason="budget"``: we hit the self-imposed per-run call cap
        (``LLM_MAX_CALLS_PER_RUN``).

    The pipeline catches this, stops cleanly, and reports whatever finished —
    so the user always gets the jobs that completed before the wall was hit.
    """

    def __init__(self, message: str, reason: str = "daily") -> None:
        self.reason = reason
        super().__init__(message)

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def _detect_provider() -> tuple[str, str, str]:
    """Return (base_url, model, api_key) based on environment variables.

    Reads env at call time (not module import time) so that load_env() called
    in _bootstrap() is always visible here.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    local_url = os.environ.get("LLM_URL", "").strip()
    model_override = os.environ.get("LLM_MODEL", "").strip()
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()

    if provider == "gemini" and gemini_key:
        return (
            GEMINI_COMPAT_BASE,
            _gemini_model(model_override),
            gemini_key,
        )

    if provider == "openai" and openai_key:
        return (
            "https://api.openai.com/v1",
            model_override or DEFAULT_OPENAI_MODEL,
            openai_key,
        )

    if provider == "local" and local_url:
        return (
            local_url.rstrip("/"),
            model_override or DEFAULT_LOCAL_MODEL,
            os.environ.get("LLM_API_KEY", ""),
        )

    if gemini_key:
        return (
            GEMINI_COMPAT_BASE,
            _gemini_model(model_override),
            gemini_key,
        )

    if openai_key:
        return (
            "https://api.openai.com/v1",
            model_override or DEFAULT_OPENAI_MODEL,
            openai_key,
        )

    if local_url:
        return (
            local_url.rstrip("/"),
            model_override or DEFAULT_LOCAL_MODEL,
            os.environ.get("LLM_API_KEY", ""),
        )

    raise RuntimeError(
        "No LLM provider configured. "
        "Set GEMINI_API_KEY, OPENAI_API_KEY, or LLM_URL in your environment."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_TIMEOUT = 120  # seconds

# Base wait on first 429/503 (doubles each retry, caps at 60s).
# Gemini free tier is 15 RPM = 4s minimum between requests; 10s gives headroom.
_RATE_LIMIT_BASE_WAIT = 10


# ---------------------------------------------------------------------------
# Per-run budget + proactive pacing
# ---------------------------------------------------------------------------
#
# On the free tier the scarce resource isn't speed, it's the number of calls.
# We (a) pace requests so we don't trip the per-minute limit and waste retries,
# and (b) optionally cap total calls so a run can reserve enough budget to
# finish a few jobs end-to-end. Both are configured at run start (frugal mode)
# and read here. State is process-global and lock-guarded because discovery
# and the streaming pipeline use worker threads.

_budget_lock = threading.Lock()
_call_count = 0
_last_call_time: float = 0.0
_cancel_requested = False  # set by the web "Stop" button

# Defaults; overridden by configure_budget() or the matching env vars.
_min_interval: float = 0.0   # seconds between calls (0 = no pacing)
_max_calls: int = 0          # 0 = unlimited


def configure_budget(min_interval: float | None = None, max_calls: int | None = None) -> None:
    """Set pacing/budget for the current run (frugal mode calls this).

    Args:
        min_interval: Minimum seconds between LLM calls (proactive rate-limit
            avoidance). None leaves it unchanged.
        max_calls: Hard cap on LLM calls this run; raises QuotaExhausted when
            reached. 0 = unlimited. None leaves it unchanged.
    """
    global _min_interval, _max_calls
    with _budget_lock:
        if min_interval is not None:
            _min_interval = max(0.0, min_interval)
        if max_calls is not None:
            _max_calls = max(0, max_calls)


def reset_run_counter() -> None:
    """Reset the per-run call counter (call once at the start of a pipeline run)."""
    global _call_count, _last_call_time
    with _budget_lock:
        _call_count = 0
        _last_call_time = 0.0


def request_cancel() -> None:
    """Ask the current run to stop. The next LLM call raises QuotaExhausted."""
    global _cancel_requested
    with _budget_lock:
        _cancel_requested = True


def clear_cancel() -> None:
    """Clear the cancel flag (call at the start of a run)."""
    global _cancel_requested
    with _budget_lock:
        _cancel_requested = False


def get_call_count() -> int:
    """Number of LLM calls made since the last reset."""
    with _budget_lock:
        return _call_count


def _effective_min_interval() -> float:
    env = os.environ.get("LLM_MIN_INTERVAL_SEC")
    if env:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    return _min_interval


def _effective_max_calls() -> int:
    env = os.environ.get("LLM_MAX_CALLS_PER_RUN")
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            pass
    return _max_calls


def _enforce_budget_and_pace() -> int:
    """Charge one call against the budget and pace it. Returns the new count.

    Raises QuotaExhausted(reason="budget") if the per-run cap is reached.
    """
    global _call_count, _last_call_time
    with _budget_lock:
        if _cancel_requested:
            raise QuotaExhausted("Run stopped by user.", reason="cancelled")
        max_calls = _effective_max_calls()
        if max_calls and _call_count >= max_calls:
            raise QuotaExhausted(
                f"Reached this run's LLM call budget ({max_calls} calls).",
                reason="budget",
            )

        # Proactive pacing: stay under the per-minute limit so we don't burn
        # retry attempts reacting to 429s. Holding the lock while sleeping is
        # intentional — it serializes pacing across worker threads.
        interval = _effective_min_interval()
        if interval > 0 and _last_call_time:
            elapsed = time.monotonic() - _last_call_time
            if elapsed < interval:
                time.sleep(interval - elapsed)

        _last_call_time = time.monotonic()
        _call_count += 1
        count = _call_count

    events.emit("llm.call", f"LLM call #{count}", count=count)
    return count


def _is_daily_quota(resp: httpx.Response) -> bool:
    """True if a 429 is a *daily* quota exhaustion (not the per-minute limit).

    Gemini's per-minute and per-day limits both return HTTP 429 with status
    RESOURCE_EXHAUSTED. They're distinguished by the quota id in the body:
    ``...PerDay...`` vs ``...PerMinute...``. Per-day cannot recover within this
    run, so we stop; per-minute is left to the normal backoff+retry path.
    """
    try:
        body = resp.text.lower()
    except Exception:
        return False
    return "perday" in body or "per day" in body


class LLMClient:
    """Thin LLM client supporting OpenAI-compatible and native Gemini endpoints.

    For Gemini keys, starts on the OpenAI-compat layer. On a 403 (which
    happens with preview/experimental models not exposed via compat), it
    automatically switches to the native generateContent API and stays there
    for the lifetime of the process.
    """

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT)
        # True once we've confirmed the native Gemini API works for this model
        self._use_native_gemini: bool = False
        self._is_gemini: bool = base_url.startswith(GEMINI_COMPAT_BASE)

    # -- Native Gemini API --------------------------------------------------

    def _chat_native_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the native Gemini generateContent API.

        Used automatically when the OpenAI-compat endpoint returns 403,
        which happens for preview/experimental models not exposed via compat.

        Converts OpenAI-style messages to Gemini's contents/systemInstruction
        format transparently.
        """
        contents: list[dict] = []
        system_parts: list[dict] = []

        for msg in messages:
            role = msg["role"]
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                # Gemini uses "model" instead of "assistant"
                contents.append({"role": "model", "parts": [{"text": text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        url = f"{GEMINI_NATIVE_BASE}/models/{self.model}:generateContent"
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            params={"key": self.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # -- OpenAI-compat API --------------------------------------------------

    def _chat_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the OpenAI-compatible endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # NVIDIA NIM hosts some reasoning models with "thinking" enabled by
        # default, which pollutes structured output (score JSON, resume text)
        # with <think> reasoning blocks. Explicitly disable it. Only sent to
        # NVIDIA's endpoint — other OpenAI-compat providers reject this field.
        if "integrate.api.nvidia.com" in self.base_url:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )

        # 403 on Gemini compat = model not available on compat layer.
        # Raise a specific sentinel so chat() can switch to native API.
        if resp.status_code == 403 and self._is_gemini:
            raise _GeminiCompatForbidden(resp)

        return self._handle_compat_response(resp)

    @staticmethod
    def _handle_compat_response(resp: httpx.Response) -> str:
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        # Charge against the per-run budget and pace before the request.
        # Raises QuotaExhausted(reason="budget") if the cap is hit.
        _enforce_budget_and_pace()

        for attempt in range(_MAX_RETRIES):
            try:
                # Route to native Gemini if we've already confirmed it's needed
                if self._use_native_gemini:
                    return self._chat_native_gemini(messages, temperature, max_tokens)

                return self._chat_compat(messages, temperature, max_tokens)

            except _GeminiCompatForbidden:
                # Model not available on OpenAI-compat layer — switch to native.
                log.warning(
                    "Gemini compat endpoint returned 403 for model '%s'. "
                    "Switching to native generateContent API. "
                    "(Preview/experimental models are often compat-only on native.)",
                    self.model,
                )
                self._use_native_gemini = True
                # Retry immediately with native — don't count as a rate-limit wait
                try:
                    return self._chat_native_gemini(messages, temperature, max_tokens)
                except httpx.HTTPStatusError as native_exc:
                    raise RuntimeError(
                        f"Both Gemini endpoints failed. Compat: 403 Forbidden. "
                        f"Native: {native_exc.response.status_code} — "
                        f"{native_exc.response.text[:200]}"
                    ) from native_exc

            except httpx.HTTPStatusError as exc:
                resp = exc.response
                if resp.status_code == 429 and _is_daily_quota(resp):
                    # Daily quota gone — retrying this run is futile. Stop cleanly
                    # so the pipeline can keep whatever already finished.
                    events.emit(
                        "quota.exhausted",
                        "Daily API quota reached — stopping so finished jobs are kept.",
                        reason="daily",
                    )
                    raise QuotaExhausted(
                        "Daily API quota reached (e.g. Gemini free tier). "
                        "Already-finished jobs were saved. Try again tomorrow, "
                        "or add a paid/local key for more headroom.",
                        reason="daily",
                    ) from exc
                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                    # Respect Retry-After header if provided (Gemini sends this).
                    retry_after = (
                        resp.headers.get("Retry-After")
                        or resp.headers.get("X-RateLimit-Reset-Requests")
                    )
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except (ValueError, TypeError):
                            wait = _RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)

                    log.warning(
                        "LLM rate limited (HTTP %s). Waiting %ds before retry %d/%d. "
                        "Tip: Gemini free tier = 15 RPM. Consider a paid account "
                        "or switching to a local model.",
                        resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                    )
                    events.emit(
                        "llm.rate_limited",
                        f"Rate limited — waiting {wait:.0f}s (retry {attempt + 1}/{_MAX_RETRIES})",
                        wait=wait, attempt=attempt + 1, status=resp.status_code,
                    )
                    time.sleep(wait)
                    continue
                raise

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    events.emit(
                        "llm.timeout",
                        f"Model slow to respond — retrying in {wait}s "
                        f"(attempt {attempt + 1}/{_MAX_RETRIES})",
                        wait=wait, attempt=attempt + 1,
                    )
                    time.sleep(wait)
                    continue
                events.emit("llm.timeout",
                            "Model did not respond after retries — skipping this step.")
                raise

        raise RuntimeError("LLM request failed after all retries")

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()


class _GeminiCompatForbidden(Exception):
    """Sentinel: Gemini OpenAI-compat returned 403. Switch to native API."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Gemini compat 403: {response.text[:200]}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton."""
    global _instance
    if _instance is None:
        base_url, model, api_key = _detect_provider()
        log.info("LLM provider: %s  model: %s", base_url, model)
        _instance = LLMClient(base_url, model, api_key)
    return _instance


def reset_client() -> None:
    """Drop the cached client so the next call re-reads provider/key from env.

    Used after the web Settings page writes a new API key, so the change takes
    effect without restarting the server.
    """
    global _instance
    if _instance is not None:
        try:
            _instance.close()
        except Exception:
            pass
    _instance = None
