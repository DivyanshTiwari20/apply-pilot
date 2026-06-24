# ApplyPilot

**Autonomous job application pipeline. Discovers jobs, scores them with AI, tailors your resume, writes cover letters, and submits applications — hands-free.**

Everything runs on your own machine. No account, no server, no data sent anywhere. Your resume, API keys, and generated documents stay on your computer.

---

## Two Ways to Use It

### Web UI — no command line needed

```bash
pip install "applypilot[web]"
applypilot serve
```

Opens `http://127.0.0.1:8000` in your browser. A built-in wizard walks you through setup (API key, resume, profile, job searches). Run the pipeline with buttons and watch live progress.

### CLI — for power users

```bash
applypilot init       # one-time setup
applypilot run        # discover → score → tailor → cover letters
applypilot apply      # autonomous browser-driven submission
```

---

## Install

```bash
# 1. Install ApplyPilot
pip install "applypilot[web]"

# 2. Install the job-board scraper separately
pip install --no-deps python-jobspy
pip install pydantic tls-client requests markdownify regex

# 3. Browser engine (needed for auto-apply)
playwright install chromium
```

> **Why is `python-jobspy` installed separately?** It pins an exact numpy version that conflicts with pip's resolver but works fine at runtime. The `--no-deps` flag skips the resolver; the second line installs its actual dependencies.

**CLI only (no web UI)?** `pip install applypilot` is enough.

---

## What You Need

| To do this | You need |
|------------|----------|
| Discover & browse jobs | Python 3.11+ |
| AI scoring, resume tailoring, cover letters | + a free Gemini API key ([get one here](https://aistudio.google.com)) |
| Autonomous auto-apply | + Chrome, [Claude Code CLI](https://claude.ai/code), and Node.js 18+ |

Start with just Python and a free Gemini API key — the rest is optional.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs + 48 Workday portals + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI extraction |
| **3. Score** | AI rates every job 1-10 against your resume. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job — reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Cover Letter** | AI writes a targeted cover letter per job |
| **6. Auto-Apply** | Claude Code navigates forms, fills fields, uploads documents, answers questions, and submits |

Each stage is independent — run all of them or only what you need.

---

## Requirements

| Component | Required For |
|-----------|-------------|
| Python 3.11+ | Everything |
| Gemini API key | Scoring, tailoring, cover letters (free tier works) |
| Node.js 18+ | Auto-apply |
| Chrome/Chromium | Auto-apply |
| Claude Code CLI | Auto-apply |

**Optional:** CapSolver API key — solves CAPTCHAs during auto-apply. Without it, CAPTCHA-blocked applications fail gracefully.

---

## Configuration

Files live in `~/.applypilot/` and are created by the web wizard or `applypilot init`:

- **`profile.json`** — contact info, work authorization, compensation, skills, resume facts
- **`searches.yaml`** — job titles, locations, boards to search
- **`.env`** — API keys (`GEMINI_API_KEY`, `LLM_MODEL`, `CAPSOLVER_API_KEY`)

---

## CLI Reference

```
applypilot serve                     # Launch the web UI
applypilot serve --port 3000         # Different port
applypilot serve --no-browser        # Don't auto-open browser
applypilot init                      # First-time setup wizard
applypilot doctor                    # Check what's installed / missing
applypilot run                       # Run full pipeline
applypilot run --workers 4           # Parallel discovery/enrichment
applypilot run --min-score 8         # Override score threshold
applypilot run --dry-run             # Preview without executing
applypilot run --validation lenient  # Relaxed validation (good for free tier)
applypilot apply                     # Auto-apply to scored jobs
applypilot apply --workers 3         # Parallel browser workers
applypilot apply --dry-run           # Fill forms without submitting
applypilot apply --headless          # Headless browser
applypilot status                    # Pipeline statistics
applypilot dashboard                 # Open results dashboard
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `applypilot: command not found` | Add Python scripts to PATH, or use `pipx install applypilot` |
| Web UI won't start | Install the web extra: `pip install "applypilot[web]"` |
| No jobs found | Check `searches.yaml` titles/location. Job boards rate-limit — try again or use `--workers 1` |
| Scoring stops partway (free tier) | Expected on Gemini's free quota. ApplyPilot runs frugal mode by default — run again later or use a paid key |
| LinkedIn links don't open | LinkedIn links expire and require login. Workday/direct site links work reliably |
| Auto-apply does nothing | Needs Chrome + Claude Code CLI + Node.js. Run `applypilot doctor` |

---

## License

[GNU Affero General Public License v3.0](LICENSE)
