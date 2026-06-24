<!-- logo here -->

> **⚠️ ApplyPilot** is the original open-source project, created by [Pickle-Pixel](https://github.com/Pickle-Pixel) and first published on GitHub on **February 17, 2026**. We are **not affiliated** with applypilot.app, useapplypilot.com, or any other product using the "ApplyPilot" name. These sites are **not associated with this project** and may misrepresent what they offer. If you're looking for the autonomous, open-source job application agent — you're in the right place.

# ApplyPilot

**Applied to 1,000 jobs in 2 days. Fully autonomous. Open source.**

[![PyPI version](https://img.shields.io/pypi/v/applypilot?color=blue)](https://pypi.org/project/applypilot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Pickle-Pixel/ApplyPilot?style=social)](https://github.com/Pickle-Pixel/ApplyPilot)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/S6S01UL5IO)




https://github.com/user-attachments/assets/7ee3417f-43d4-4245-9952-35df1e77f2df


---

## What It Does

ApplyPilot is a 6-stage autonomous job application pipeline. It discovers jobs across 5+ boards, scores them against your resume with AI, tailors your resume per job, writes cover letters, and **submits applications for you**. It navigates forms, uploads documents, answers screening questions, all hands-free.

**Everything runs on your own machine.** ApplyPilot is not a hosted service — there's no account to create and no server to send your data to. Your resume, profile, API keys, and generated documents stay on your computer. You bring your own (free) API key. It's open source, so you can read exactly what it does.

---

## Two Ways to Use It

You can use ApplyPilot entirely from a **browser** (no command line needed) or from the **CLI**. Both do the same work and share the same local data.

### 🖥️ Web UI — easiest, no command line needed

Install once, then run a single command to open the app in your browser. A built-in setup wizard walks you through your API key, resume, profile, and job searches — then you run everything with buttons and watch progress live.

```bash
pip install "applypilot[web]"
applypilot serve            # opens http://127.0.0.1:8000 in your browser
```

That's it. The web UI takes you from job discovery → AI scoring → tailored resume + cover letter (downloadable PDFs) → a direct **Apply** link for each job. *(Autonomous form-filling is CLI-only — see below.)*

### ⌨️ CLI — for power users and automation

```bash
applypilot init          # one-time setup: resume, profile, preferences, API keys
applypilot doctor        # verify your setup — shows what's installed and what's missing
applypilot run           # discover > enrich > score > tailor > cover letters
applypilot run -w 4      # same but parallel (4 threads for discovery/enrichment)
applypilot apply         # autonomous browser-driven submission
applypilot apply -w 3    # parallel apply (3 Chrome instances)
applypilot apply --dry-run  # fill forms without submitting
```

---

## Install

```bash
# 1. Install ApplyPilot (the [web] extra adds the browser UI — recommended)
pip install "applypilot[web]"

# 2. Install the job-board scraper separately (see note below)
pip install --no-deps python-jobspy
pip install pydantic tls-client requests markdownify regex

# 3. (Optional) browser engine — needed for some scrapers and for auto-apply
playwright install chromium
```

> **Why is `python-jobspy` installed separately?** It pins an exact numpy version in its metadata that conflicts with pip's resolver, but works fine at runtime with any modern numpy. The `--no-deps` flag bypasses the resolver; the second line installs jobspy's actual runtime dependencies. Everything else installs normally.

**Prefer the CLI only?** `pip install applypilot` (without `[web]`) is enough.

After installing, either run **`applypilot serve`** for the web UI or **`applypilot init`** for the CLI setup wizard.

---

## What you need

| To do this | You need |
|------------|----------|
| Discover & browse jobs | Python 3.11+ |
| AI scoring, resume tailoring, cover letters | + a **free** Gemini API key ([get one here](https://aistudio.google.com)) |
| Autonomous auto-apply | + Chrome, [Claude Code CLI](https://claude.ai/code), and Node.js 18+ |

The web UI and CLI both unlock more features as you add these — start with just Python and a free API key.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes 5 job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) + 48 Workday employer portals + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | AI rates every job 1-10 based on your resume and preferences. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Cover Letter** | AI generates a targeted cover letter per job |
| **6. Auto-Apply** | Claude Code navigates application forms, fills fields, uploads documents, answers questions, and submits |

Each stage is independent. Run them all or pick what you need.

---

## ApplyPilot vs The Alternatives

| Feature | ApplyPilot | AIHawk | Manual |
|---------|-----------|--------|--------|
| Job discovery | 5 boards + Workday + direct sites | LinkedIn only | One board at a time |
| AI scoring | 1-10 fit score per job | Basic filtering | Your gut feeling |
| Resume tailoring | Per-job AI rewrite | Template-based | Hours per application |
| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |
| Supported sites | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, 46 Workday portals, 28 direct sites | LinkedIn | Whatever you open |
| License | AGPL-3.0 | MIT | N/A |

---

## Requirements

| Component | Required For | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Node.js 18+ | Auto-apply | Needed for `npx` to run Playwright MCP server |
| Gemini API key | Scoring, tailoring, cover letters | Gemini Flash-Lite free tier is the fast default |
| Chrome/Chromium | Auto-apply | Auto-detected on most systems |
| Claude Code CLI | Auto-apply | Install from [claude.ai/code](https://claude.ai/code) |

**Gemini API key is free.** Get one at [aistudio.google.com](https://aistudio.google.com). OpenAI and local models (Ollama/llama.cpp) are also supported.

### Optional

| Component | What It Does |
|-----------|-------------|
| CapSolver API key | Solves CAPTCHAs during auto-apply (hCaptcha, reCAPTCHA, Turnstile, FunCaptcha). Without it, CAPTCHA-blocked applications just fail gracefully |

> **Note:** python-jobspy is installed separately with `--no-deps` because it pins an exact numpy version in its metadata that conflicts with pip's resolver. It works fine with modern numpy at runtime.

---

## Configuration

These files live in `~/.applypilot/` and are created for you by the **web setup wizard** (`applypilot serve`) or the **CLI wizard** (`applypilot init`) — you don't normally edit them by hand:

### `profile.json`
Your personal data in one structured file: contact info, work authorization, compensation, experience, skills, resume facts (preserved during tailoring), and EEO defaults. Powers scoring, tailoring, and form auto-fill.

### `searches.yaml`
Job search queries, target titles, locations, boards. Run multiple searches with different parameters.

### `.env`
API keys and runtime config: `LLM_PROVIDER`, `GEMINI_API_KEY`, `LLM_MODEL`, `CAPSOLVER_API_KEY` (optional).

### Package configs (shipped with ApplyPilot)
- `config/employers.yaml` - Workday employer registry (48 preconfigured)
- `config/sites.yaml` - Direct career sites (30+), blocked sites, base URLs, manual ATS domains
- `config/searches.example.yaml` - Example search configuration

---

## How Stages Work

### Discover
Queries Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs via JobSpy. Scrapes 48 Workday employer portals (configurable in `employers.yaml`). Hits 30 direct career sites with custom extractors. Deduplicates by URL.

### Enrich
Visits each job URL and extracts the full description. 3-tier cascade: JSON-LD structured data, then CSS selector patterns, then AI-powered extraction for unknown layouts.

### Score
AI scores every job 1-10 against your profile. 9-10 = strong match, 7-8 = good, 5-6 = moderate, 1-4 = skip. Only jobs above your threshold proceed to tailoring.

### Tailor
Generates a custom resume per job: reorders experience, emphasizes relevant skills, incorporates keywords from the job description. Your `resume_facts` (companies, projects, metrics) are preserved exactly. The AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job referencing the specific company, role, and how your experience maps to their requirements.

### Auto-Apply
Claude Code launches a Chrome instance, navigates to each application page, detects the form type, fills personal information and work history, uploads the tailored resume and cover letter, answers screening questions with AI, and submits. A live dashboard shows progress in real-time.

The Playwright MCP server is configured automatically at runtime per worker. No manual MCP setup needed.

```bash
# Utility modes (no Chrome/Claude needed)
applypilot apply --mark-applied URL    # manually mark a job as applied
applypilot apply --mark-failed URL     # manually mark a job as failed
applypilot apply --reset-failed        # reset all failed jobs for retry
applypilot apply --gen --url URL       # generate prompt file for manual debugging
```

---

## CLI Reference

```
applypilot serve                        # Launch the web UI (browser app)
applypilot serve --port 3000            # Use a different port
applypilot serve --no-browser           # Don't auto-open the browser
applypilot init                         # First-time setup wizard (CLI)
applypilot doctor                       # Verify setup, diagnose missing requirements
applypilot run [stages...]              # Run pipeline stages (or 'all')
applypilot run --workers 4              # Parallel discovery/enrichment
applypilot run --stream                 # Concurrent stages (streaming mode)
applypilot run --min-score 8            # Override score threshold
applypilot run --dry-run                # Preview without executing
applypilot run --validation lenient     # Relax validation (recommended for Gemini free tier)
applypilot run --validation strict      # Strictest validation (retries on any banned word)
applypilot apply                        # Launch auto-apply
applypilot apply --workers 3            # Parallel browser workers
applypilot apply --dry-run              # Fill forms without submitting
applypilot apply --continuous           # Run forever, polling for new jobs
applypilot apply --headless             # Headless browser mode
applypilot apply --url URL              # Apply to a specific job
applypilot status                       # Pipeline statistics
applypilot dashboard                    # Open HTML results dashboard
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `applypilot: command not found` | Make sure your Python scripts directory is on your `PATH`, or try `python -m applypilot`. On some systems use `pipx install applypilot` for an isolated, always-on-PATH install. |
| Web UI won't start / "needs extra packages" | Install the web extra: `pip install "applypilot[web]"`. |
| No jobs found during discovery | Check your `searches.yaml` titles/location, and confirm `python-jobspy` installed (see Install). Job boards also rate-limit — try again later or use `--workers 1`. |
| Scoring/tailoring stops partway on the free tier | This is expected on Gemini's free quota. ApplyPilot runs in **frugal mode** by default to finish a few jobs completely before the quota runs out. Run again later, or add a paid/local key. |
| LinkedIn "job posting removed" when clicking Apply | LinkedIn listing links expire or require login. Jobs from Workday/direct career sites give working apply links. |
| Auto-apply (`applypilot apply`) does nothing | It needs Chrome + the [Claude Code CLI](https://claude.ai/code) + Node.js. Run `applypilot doctor` to see what's missing. |

Run **`applypilot doctor`** any time to see what's installed and what's missing.

---

## Contributing

ApplyPilot is open source (AGPL-3.0) and contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines. Please also read our [Code of Conduct](CODE_OF_CONDUCT.md). To report a security issue, see [SECURITY.md](SECURITY.md).

---

## License

ApplyPilot is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
"# apply-pilot" 
