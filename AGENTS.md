# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Quick Commands

**Development Setup**
```bash
pip install -e ".[dev]"
playwright install chromium
```

**Testing & Linting**
```bash
# Run all tests
pytest tests/ -v

# Run specific test
pytest tests/test_scoring.py::test_name -v

# Lint check (Ruff)
ruff check src/

# Auto-fix formatting
ruff check src/ --fix
ruff format src/
```

**Running ApplyPilot**
```bash
# One-time setup
applypilot init

# Verify setup
applypilot doctor

# Full pipeline
applypilot run
applypilot run --workers 4  # parallel discovery/enrichment
applypilot run discover enrich  # specific stages only

# Auto-apply with Chrome
applypilot apply
applypilot apply --workers 3 --headless

# Dashboard
applypilot dashboard
applypilot status
```

## Architecture Overview

ApplyPilot is a **6-stage autonomous job application pipeline** orchestrated by `pipeline.py`. Each stage is independent and can be run separately.

### The Pipeline Stages

| Stage | Module | Purpose |
|-------|--------|---------|
| **Discover** | `discovery/` | Scrapes 5+ job boards (JobSpy), 48 Workday employer portals, 30+ direct career sites |
| **Enrich** | `enrichment/` | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **Score** | `scoring/scorer.py` | AI rates jobs 1-10 against user profile (gates tailor/cover with min-score threshold) |
| **Tailor** | `scoring/tailor.py` | AI rewrites resume per job: reorganizes experience, emphasizes relevant skills, adds keywords |
| **Cover Letter** | `scoring/cover_letter.py` | AI generates targeted cover letters per job |
| **PDF** | `scoring/pdf.py` | Converts tailored resumes + cover letters to PDF |
| **Auto-Apply** | `apply/` | Codex navigates forms, fills fields, uploads docs, answers questions, submits |

### Key Modules

- **`cli.py`**: Typer-based CLI entry point. Defines all user-facing commands (run, apply, init, doctor, etc.)
- **`config.py`**: Environment setup, config loading (profile.json, searches.yaml), directory initialization
- **`database.py`**: SQLite persistence for jobs, applications, scores, tailored resumes. Central source of truth for pipeline state
- **`llm.py`**: LLM provider abstraction (Gemini, OpenAI, local via Ollama). Handles API calls, token counting, validation
- **`wizard/init.py`**: First-time setup wizard—creates profile.json, searches.yaml, .env file
- **`view.py`**: Rich-based UI output (tables, panels, progress bars)

### Discovery Modules

- **`discovery/jobspy.py`**: Wraps python-jobspy for Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs
- **`discovery/workday.py`**: Scrapes 48 preconfigured Workday employer portals (from config/employers.yaml)
- **`discovery/smartextract.py`**: AI-powered scraper for 30+ direct career sites (from config/sites.yaml)

### Scoring Modules

- **`scoring/scorer.py`**: Scores jobs 1-10 against profile. Only jobs >= min_score proceed to tailor
- **`scoring/tailor.py`**: Generates tailored resume (reorganizes, adds keywords, preserves resume_facts)
- **`scoring/validator.py`**: Checks tailored resume for banned words, LLM judge for quality
- **`scoring/cover_letter.py`**: Generates cover letters. Also validates for banned words

### Apply Stage

- **`apply/launcher.py`**: Orchestrates parallel Chrome workers and form submission
- **`apply/chrome.py`**: Low-level Chrome control via Playwright (form detection, field filling, CAPTCHA handling)
- **`apply/prompt.py`**: Generates Codex prompts for auto-apply. Used by MCP server at runtime
- **`apply/dashboard.py`**: HTML dashboard showing application progress

## Database Schema & State

Jobs flow through `status` states:
```
discovered → enriched → scored → tailored → cover_generated → applied
                                   ↓
                            (rejected if score < min_score)
```

Key tables in SQLite DB:
- `jobs`: discovered job listings
- `scores`: job fit scores (1-10)
- `resumes`: tailored resumes (cached per job)
- `applications`: apply status and submission results

Query the database via `database.py`:
- `get_connection()`: get SQLite connection
- `get_stats()`: aggregate job counts by status
- Job objects are dicts with keys: `job_id, title, company, salary, apply_url, status, score, ...`

## LLM Integration

ApplyPilot uses an LLM abstraction in `llm.py` to support Gemini (default), OpenAI, and local models.

**Key points:**
- All LLM calls go through `get_client()` (returns anthropic.Anthropic, openai.OpenAI, or ollama.Ollama)
- Scoring, tailoring, and cover letter generation use system prompts stored as strings in the respective modules
- Validation runs a "judge" prompt to ensure quality (configurable strictness: strict/normal/lenient)
- Token counting is used to warn if context becomes large

**Environment variables** (in `.env`):
- `GEMINI_API_KEY`: Gemini Flash-Lite free tier is the fast default
- `LLM_MODEL`: e.g., "gemini-3.1-flash-lite", "gpt-4o", "Codex-3-5-sonnet-20241022"
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`: For other providers
- `CAPSOLVER_API_KEY`: Optional, for solving CAPTCHAs during apply stage

## Config Files (User-Provided)

All generated by `applypilot init`:

- **`profile.json`**: User's contact info, work auth, compensation, experience, resume facts (preserved during tailoring)
- **`searches.yaml`**: Job search queries, target titles, locations, preferred boards
- **`.env`**: API keys and runtime config

Shipped with package:
- **`config/employers.yaml`**: 48 Workday employer portals (tenant ID, instance, URL)
- **`config/sites.yaml`**: 30+ direct career sites with CSS selectors and enrichment metadata
- **`config/searches.example.yaml`**: Example search configuration

## Common Patterns

### Parallel Execution
Discovery and enrichment stages support `--workers N` to run tasks in parallel threads. The pipeline coordinates thread-safe DB writes via SQLite locking.

### Streaming Mode
`--stream` flag runs stages concurrently (pipelined) instead of sequentially. Each stage pulls work from the previous stage's output queue.

### Dry-Run Mode
`--dry-run` executes all logic without persisting to DB or submitting applications. Useful for testing and debugging.

### Validation Strictness
The `--validation` flag controls how strict the LLM validator is:
- **strict**: Any banned word → error, LLM judge must pass
- **normal** (default): Banned words → warnings only (recommended for Gemini free tier)
- **lenient**: No validation, fastest API calls

## Testing

Tests live in `tests/`. Use pytest:

```bash
# Run all
pytest tests/ -v

# Run one file
pytest tests/test_scoring.py -v

# Run one test
pytest tests/test_scoring.py::test_scorer_basic -v

# Coverage
pytest tests/ --cov=src/applypilot --cov-report=term-missing
```

When adding tests:
- Mock external API calls (LLM, job boards) to avoid rate limits
- Use fixtures for profile, searches, sample jobs
- Test both happy path and edge cases (empty results, network errors, etc.)

## Code Style & Requirements

- **Python**: 3.11+ (use type hints everywhere)
- **Linting**: Ruff (target-version = py311, line-length = 120)
- **Import order**: Ruff-sorted (isort-compatible)
- **Docstrings**: Google style for public functions/classes
- **Type hints**: Required on all function signatures

CI runs `ruff check src/` and `pytest tests/ -v` on every PR. All must pass.

## License & Contributions

This is an AGPL-3.0 project. When contributing:
1. Open an issue first for new features (discuss approach)
2. Create feature branch from `main`
3. Add tests and update CHANGELOG.md under `[Unreleased]`
4. Run `ruff check src/ --fix` and `pytest tests/ -v` before submitting PR
5. Keep PRs focused (one feature per PR)

See CONTRIBUTING.md for detailed guidelines, especially for adding new Workday employers or direct career sites.

## MCP & Codex Integration

The `apply/` stage uses Codex CLI with an MCP (Model Context Protocol) server for browser automation:
- The Playwright MCP server is configured at runtime in `apply/prompt.py`
- ApplyPilot generates a custom prompt per worker that describes the application form and required fields
- Codex (via MCP) navigates forms, fills fields, uploads documents, and submits
- No manual MCP setup is needed—it's all done in `apply/launcher.py`
