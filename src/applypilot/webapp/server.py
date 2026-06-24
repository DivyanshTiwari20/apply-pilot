"""FastAPI app for ApplyPilot's local web UI.

Exposes the existing pipeline as a small REST API plus a Server-Sent Events
stream that pushes every pipeline step to the browser live. Local single-user:
reads/writes the same ~/.applypilot data the CLI uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as e:  # pragma: no cover - clear message if extra missing
    raise SystemExit(
        "The web app needs extra packages. Install them with:\n"
        '    pip install -e ".[web]"'
    ) from e

from applypilot import config
from applypilot.database import get_connection, get_stats
from applypilot.llm import DEFAULT_GEMINI_MODEL, DEFAULT_OPENAI_MODEL, reset_client
from applypilot.webapp import runner

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="ApplyPilot", docs_url="/api/docs", openapi_url="/api/openapi.json")

# Allow the Next.js dev server (and any localhost port) to call the API during
# development. Local-only app, so this is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _clean_url(value: Any) -> str | None:
    """Normalize a stored URL: treat empty / 'None' / non-http values as missing.

    Scrapers sometimes persist the literal string ``"None"`` (or a relative
    fragment) into ``application_url``. Returning those would produce broken
    links like ``http://127.0.0.1:8000/None`` in the UI, so we drop anything
    that isn't a real absolute http(s) URL.
    """
    if not value:
        return None
    s = str(value).strip()
    if s.lower() in ("none", "null", "n/a", ""):
        return None
    if not s.startswith(("http://", "https://")):
        return None
    return s


def _job_row(row) -> dict[str, Any]:
    d = dict(row)
    return {
        "url": _clean_url(d.get("url")),
        "title": d.get("title"),
        "company": d.get("site"),
        "location": d.get("location"),
        "score": d.get("fit_score"),
        "reasoning": d.get("score_reasoning"),
        "status": _derive_status(d),
        "apply_url": _clean_url(d.get("application_url")),
        "resume_path": d.get("tailored_resume_path"),
        "cover_path": d.get("cover_letter_path"),
        "scored_at": d.get("scored_at"),
        "tailored_at": d.get("tailored_at"),
    }


def _derive_status(d: dict) -> str:
    if d.get("applied_at"):
        return "applied"
    if d.get("cover_letter_path"):
        return "ready"
    if d.get("tailored_resume_path"):
        return "tailored"
    if d.get("fit_score") is not None:
        return "scored"
    if d.get("full_description"):
        return "enriched"
    return "discovered"


# ── Status & stats ─────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
def status() -> dict:
    """Setup readiness + current run state (drives the UI's top bar)."""
    config.load_env()
    has_key = bool(
        __import__("os").environ.get("GEMINI_API_KEY")
        or __import__("os").environ.get("OPENAI_API_KEY")
        or __import__("os").environ.get("LLM_URL")
    )
    return {
        "has_api_key": has_key,
        "has_profile": config.PROFILE_PATH.exists(),
        "has_resume": config.RESUME_PATH.exists(),
        "tier": config.get_tier(),
        "run": runner.get_state(),
    }


@app.get("/api/stats")
def stats() -> dict:
    return get_stats()


@app.get("/api/jobs")
def jobs(min_score: int = 0, limit: int = 200) -> dict:
    """List jobs for the dashboard, best-first."""
    conn = get_connection()
    where = "WHERE fit_score >= ?" if min_score > 0 else ""
    params: list = [min_score] if min_score > 0 else []
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM jobs {where} "
        "ORDER BY fit_score DESC NULLS LAST, discovered_at DESC LIMIT ?",
        params,
    ).fetchall()
    return {"jobs": [_job_row(r) for r in rows]}


# ── Run control ────────────────────────────────────────────────────────────

@app.post("/api/run")
async def run(req: Request) -> JSONResponse:
    body = await req.json() if await req.body() else {}
    result = runner.start_run({
        "stages": body.get("stages") or ["all"],
        "min_score": body.get("min_score", 7),
        "max_jobs": body.get("max_jobs", 0),
        "validation": body.get("validation", "normal"),
        "workers": body.get("workers", 1),
        "frugal": body.get("frugal"),  # None = auto-detect free tier
    })
    code = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=code)


@app.get("/api/run")
def run_state() -> dict:
    return runner.get_state()


@app.post("/api/stop")
def stop_run() -> JSONResponse:
    result = runner.request_stop()
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@app.post("/api/reset")
def reset_data() -> JSONResponse:
    """Wipe all saved jobs so the user can start from a clean slate.

    Local single-user tool: clears the jobs table. Generated resume/cover files
    on disk are left alone (harmless). Refuses while a run is active.
    """
    if runner.is_running():
        return JSONResponse(
            {"ok": False, "error": "Stop the current run before resetting."},
            status_code=409,
        )
    conn = get_connection()
    deleted = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.execute("DELETE FROM jobs")
    conn.commit()
    return JSONResponse({"ok": True, "deleted": deleted})


@app.get("/api/events")
async def events_stream(request: Request) -> StreamingResponse:
    """Live Server-Sent Events stream of every pipeline step."""
    q = runner.add_subscriber()

    async def gen():
        try:
            # Replay recent history so a fresh tab sees the run already underway.
            for payload in runner.recent_events():
                yield _sse(payload)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.to_thread(q.get, True, 15)
                except queue.Empty:
                    yield ": keepalive\n\n"  # comment line keeps the connection open
                    continue
                yield _sse(payload)
        finally:
            runner.remove_subscriber(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Settings (API key) ─────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings() -> dict:
    config.load_env()
    import os
    explicit_provider = os.environ.get("LLM_PROVIDER", "").lower()
    provider = (
        explicit_provider if explicit_provider in {"gemini", "openai", "local"}
        else "gemini" if os.environ.get("GEMINI_API_KEY")
        else "openai" if os.environ.get("OPENAI_API_KEY")
        else "local" if os.environ.get("LLM_URL")
        else None
    )
    default_model = {
        "gemini": DEFAULT_GEMINI_MODEL,
        "openai": DEFAULT_OPENAI_MODEL,
        "local": "local-model",
    }.get(provider or "", "")
    model = os.environ.get("LLM_MODEL", "") or default_model
    if provider == "gemini" and model and not model.startswith("gemini-"):
        model = DEFAULT_GEMINI_MODEL
    return {
        "provider": provider,
        "model": model,
        "has_api_key": bool(
            os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        ),
    }


@app.post("/api/settings")
async def save_settings(req: Request) -> dict:
    """Persist the user's API key to ~/.applypilot/.env (replaces the wizard)."""
    body = await req.json()
    provider = (body.get("provider") or "gemini").lower()
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    default_model = {
        "gemini": DEFAULT_GEMINI_MODEL,
        "openai": DEFAULT_OPENAI_MODEL,
        "local": "local-model",
    }.get(provider, DEFAULT_GEMINI_MODEL)
    if provider == "gemini" and model and not model.startswith("gemini-"):
        model = DEFAULT_GEMINI_MODEL

    key_var = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
    }.get(provider, "GEMINI_API_KEY")

    config.ensure_dirs()
    env = _read_env(config.ENV_PATH)
    env["LLM_PROVIDER"] = provider
    if api_key:
        env[key_var] = api_key
    env["LLM_MODEL"] = model or default_model
    if provider == "gemini":
        # Prevent old OpenAI-compatible local endpoints (for example NVIDIA NIM)
        # from hijacking runs after the user saves a Gemini key.
        env.pop("LLM_URL", None)
        env.pop("LLM_API_KEY", None)
    _write_env(config.ENV_PATH, env)

    # Re-read env and drop the cached LLM client so the new key takes effect now.
    config.load_env()
    import os
    os.environ["LLM_PROVIDER"] = provider
    if api_key:
        os.environ[key_var] = api_key
    os.environ["LLM_MODEL"] = model or default_model
    if provider == "gemini":
        os.environ.pop("LLM_URL", None)
        os.environ.pop("LLM_API_KEY", None)
    reset_client()
    return {"ok": True, "provider": provider, "model": os.environ["LLM_MODEL"]}


def _read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _write_env(path: Path, env: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in env.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Onboarding: resume, profile, searches (replaces the CLI wizard) ──────────

@app.post("/api/resume")
async def save_resume(req: Request) -> JSONResponse:
    """Save the user's resume text (what scoring/tailoring read)."""
    body = await req.json()
    text = (body.get("text") or "").strip()
    if len(text) < 50:
        return JSONResponse(
            {"ok": False, "error": "Please paste your full resume text (at least a few lines)."},
            status_code=400,
        )
    config.ensure_dirs()
    config.RESUME_PATH.write_text(text, encoding="utf-8")
    return JSONResponse({"ok": True})


@app.get("/api/resume")
def get_resume() -> dict:
    text = config.RESUME_PATH.read_text(encoding="utf-8") if config.RESUME_PATH.exists() else ""
    return {"text": text}


@app.get("/api/profile")
def get_profile() -> dict:
    """Return the saved profile (or null) so the UI can prefill the setup form."""
    import json as _json
    if config.PROFILE_PATH.exists():
        try:
            return {"profile": _json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))}
        except Exception:
            return {"profile": None}
    return {"profile": None}


@app.post("/api/profile")
async def save_profile(req: Request) -> JSONResponse:
    """Persist the user's profile.json from the web setup form.

    The frontend sends a nested object matching profile.json. We fill in the
    sections the pipeline expects but the form doesn't ask about (EEO defaults,
    availability) so downstream stages never see a missing key.
    """
    import json as _json

    body = await req.json()
    personal = body.get("personal") or {}
    if not (personal.get("full_name") or "").strip():
        return JSONResponse({"ok": False, "error": "Full name is required."}, status_code=400)
    if not (personal.get("email") or "").strip():
        return JSONResponse({"ok": False, "error": "Email is required."}, status_code=400)

    profile = {
        "personal": {
            "full_name": personal.get("full_name", "").strip(),
            "preferred_name": personal.get("preferred_name", ""),
            "email": personal.get("email", "").strip(),
            "phone": personal.get("phone", ""),
            "city": personal.get("city", ""),
            "province_state": personal.get("province_state", ""),
            "country": personal.get("country", ""),
            "postal_code": personal.get("postal_code", ""),
            "address": personal.get("address", ""),
            "linkedin_url": personal.get("linkedin_url", ""),
            "github_url": personal.get("github_url", ""),
            "portfolio_url": personal.get("portfolio_url", ""),
            "website_url": personal.get("website_url", personal.get("portfolio_url", "")),
            "password": personal.get("password", ""),
        },
        "work_authorization": body.get("work_authorization") or {
            "legally_authorized_to_work": True,
            "require_sponsorship": False,
            "work_permit_type": "",
        },
        "compensation": body.get("compensation") or {
            "salary_expectation": "", "salary_currency": "USD",
            "salary_range_min": "", "salary_range_max": "",
        },
        "experience": body.get("experience") or {
            "years_of_experience_total": "", "education_level": "",
            "current_title": "", "target_role": "",
        },
        "skills_boundary": body.get("skills_boundary") or {
            "programming_languages": [], "frameworks": [], "tools": [],
        },
        "resume_facts": body.get("resume_facts") or {
            "preserved_companies": [], "preserved_projects": [],
            "preserved_school": "", "real_metrics": [],
        },
        "eeo_voluntary": {
            "gender": "Decline to self-identify",
            "race_ethnicity": "Decline to self-identify",
            "veteran_status": "Decline to self-identify",
            "disability_status": "Decline to self-identify",
        },
        "availability": body.get("availability") or {"earliest_start_date": "Immediately"},
    }

    config.ensure_dirs()
    config.PROFILE_PATH.write_text(
        _json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return JSONResponse({"ok": True})


@app.get("/api/searches")
def get_searches() -> dict:
    """Return current search config in the simple shape the setup form uses."""
    cfg = config.load_search_config() or {}
    defaults = cfg.get("defaults", {})
    locs = cfg.get("locations", [])
    titles = [q.get("query", "") for q in cfg.get("queries", []) if q.get("query")]
    return {
        "titles": titles,
        "location": defaults.get("location") or (locs[0].get("location") if locs else "Remote"),
        "remote": bool(locs[0].get("remote")) if locs else (int(defaults.get("distance", 0)) == 0),
        "distance": defaults.get("distance", 0),
        "hours_old": defaults.get("hours_old", 72),
    }


@app.post("/api/searches")
async def save_searches(req: Request) -> JSONResponse:
    """Write searches.yaml from the web setup form (replaces the wizard step)."""
    body = await req.json()
    titles = [t.strip() for t in (body.get("titles") or []) if t and t.strip()]
    if not titles:
        return JSONResponse(
            {"ok": False, "error": "Add at least one job title to search for."},
            status_code=400,
        )
    location = (body.get("location") or "Remote").strip()
    remote = bool(body.get("remote", True))
    try:
        distance = int(body.get("distance") or 0)
    except (TypeError, ValueError):
        distance = 0
    try:
        hours_old = int(body.get("hours_old") or 72)
    except (TypeError, ValueError):
        hours_old = 72

    lines = [
        "# ApplyPilot search configuration (generated by the web setup)",
        "",
        "defaults:",
        f'  location: "{location}"',
        f"  distance: {distance}",
        f"  hours_old: {hours_old}",
        "  results_per_site: 50",
        "",
        "locations:",
        f'  - location: "{location}"',
        f"    remote: {str(remote).lower()}",
        "",
        "queries:",
    ]
    for i, title in enumerate(titles):
        lines.append(f'  - query: "{title}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    config.ensure_dirs()
    config.SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return JSONResponse({"ok": True})


# ── Downloads ──────────────────────────────────────────────────────────────

@app.get("/api/download")
def download(path: str) -> FileResponse:
    """Serve a generated PDF/text, restricted to the output directories."""
    target = Path(path).resolve()
    allowed = [config.TAILORED_DIR.resolve(), config.COVER_LETTER_DIR.resolve()]
    if not any(str(target).startswith(str(base)) for base in allowed):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not target.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(target, filename=target.name)


# ── Static frontend ────────────────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
