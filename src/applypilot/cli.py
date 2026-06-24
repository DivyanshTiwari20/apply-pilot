"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from applypilot import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="applypilot",
    help="AI-powered end-to-end job application pipeline.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "pdf")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    import sys

    from applypilot.config import load_env, ensure_dirs
    from applypilot.database import init_db

    # Make console output Unicode-safe so a stray character (e.g. an arrow in a
    # log line, or a non-cp1252 char in a job title) can't crash a run on
    # Windows terminals. Replace unencodable chars instead of raising.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline."""


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted."
        ),
    ),
    min_score: int = typer.Option(7, "--min-score", help="Minimum fit score for tailor/cover stages."),
    workers: int = typer.Option(1, "--workers", "-w", help="Parallel threads for discovery/enrichment stages."),
    stream: bool = typer.Option(False, "--stream", help="Run stages concurrently (streaming mode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview stages without executing."),
    max_jobs: int = typer.Option(
        0,
        "--max-jobs",
        help=(
            "Max jobs to process through LLM stages (score/tailor/cover) per run. "
            "0 = unlimited. Recommended: 5-20 on Gemini Flash-Lite free tier."
        ),
    ),
    validation: str = typer.Option(
        "normal",
        "--validation",
        help=(
            "Validation strictness for tailor/cover stages. "
            "strict: banned words = errors, judge must pass. "
            "normal: banned words = warnings only (default, recommended for Gemini free tier). "
            "lenient: banned words ignored, LLM judge skipped (fastest, fewest API calls)."
        ),
    ),
    frugal: Optional[bool] = typer.Option(
        None,
        "--frugal/--no-frugal",
        help=(
            "Free-tier mode: process jobs depth-first (finish a few completely) "
            "and pace/cap API calls so you always get results before the quota runs "
            "out. Auto-enabled on Gemini free tier; use --no-frugal to force batch mode."
        ),
    ),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, cover, pdf."""
    _bootstrap()

    from applypilot.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier
        check_tier(2, "AI scoring/tailoring")

    # Validate the --validation flag value
    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(
            f"[red]Invalid --validation value:[/red] '{validation}'. "
            f"Choose from: {', '.join(valid_modes)}"
        )
        raise typer.Exit(code=1)

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        dry_run=dry_run,
        stream=stream,
        workers=workers,
        validation_mode=validation,
        max_jobs=max_jobs,
        frugal=frugal,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind."),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open the browser."),
) -> None:
    """Launch the local web app (browser UI) for ApplyPilot."""
    _bootstrap()

    try:
        import uvicorn  # noqa: F401
        from applypilot.webapp.server import app as web_app  # noqa: F401
    except SystemExit:
        raise
    except ImportError:
        console.print(
            '[red]The web app needs extra packages.[/red] Install them with:\n'
            '    [bold]pip install -e ".[web]"[/bold]'
        )
        raise typer.Exit(code=1)

    url = f"http://{host}:{port}"
    console.print(f"[green]ApplyPilot web app:[/green] [bold]{url}[/bold]  (Ctrl+C to stop)")

    if not no_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    import uvicorn
    uvicorn.run("applypilot.webapp.server:app", host=host, port=port, log_level="info")


@app.command()
def apply(
    mark_applied: Optional[str] = typer.Option(None, "--mark-applied", help="Manually mark a job URL as applied."),
    mark_failed: Optional[str] = typer.Option(None, "--mark-failed", help="Manually mark a job URL as failed (provide URL)."),
    fail_reason: Optional[str] = typer.Option(None, "--fail-reason", help="Reason for --mark-failed."),
    reset_failed: bool = typer.Option(False, "--reset-failed", help="Reset all failed jobs for retry."),
) -> None:
    """Manually mark job application status (auto-apply removed)."""
    _bootstrap()

    if mark_applied:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_applied, "applied")
        console.print(f"[green]Marked as applied:[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_failed, "failed", reason=fail_reason)
        console.print(f"[yellow]Marked as failed:[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.apply.launcher import reset_failed as do_reset
        count = do_reset()
        console.print(f"[green]Reset {count} failed job(s) for retry.[/green]")
        return

    console.print(
        "[yellow]Auto-apply is disabled in this build.[/yellow]\n\n"
        "After running [bold]applypilot run[/bold], your tailored resumes and cover letters\n"
        "are saved in [bold]~/.applypilot/tailored_resumes/[/bold] and [bold]~/.applypilot/cover_letters/[/bold].\n\n"
        "Use [bold]applypilot apply --mark-applied URL[/bold] to track manual applications.\n"
        "Use [bold]applypilot dashboard[/bold] to view your job pipeline."
    )


@app.command()
def status() -> None:
    """Show pipeline statistics and ready-to-apply jobs from the database."""
    _bootstrap()

    from applypilot.database import get_stats, get_connection

    stats = get_stats()
    conn = get_connection()

    console.print("\n[bold]ApplyPilot Pipeline Status[/bold]\n")

    # Pipeline funnel — shows each stage as a step with counts
    funnel = Table(title="Pipeline Funnel", show_header=True, header_style="bold cyan")
    funnel.add_column("Stage", style="bold")
    funnel.add_column("Done", justify="right")
    funnel.add_column("Pending", justify="right")
    funnel.add_column("Status")

    def _bar(done: int, total: int) -> str:
        if total == 0:
            return "[dim]no data[/dim]"
        pct = done / total
        filled = int(pct * 20)
        color = "green" if pct >= 0.8 else "yellow" if pct >= 0.4 else "red"
        return f"[{color}]{'█' * filled}{'░' * (20 - filled)}[/{color}] {pct:.0%}"

    total = stats["total"]
    funnel.add_row(
        "Discover", str(total), "—",
        "[green]complete[/green]" if total > 0 else "[dim]no jobs yet[/dim]",
    )
    funnel.add_row(
        "Enrich", str(stats["with_description"]), str(stats["pending_detail"]),
        _bar(stats["with_description"], total),
    )
    funnel.add_row(
        "Score", str(stats["scored"]), str(stats["unscored"]),
        _bar(stats["scored"], stats["with_description"]) if stats["with_description"] else "[dim]—[/dim]",
    )
    funnel.add_row(
        "Tailor", str(stats["tailored"]), str(stats["untailored_eligible"]),
        _bar(stats["tailored"], stats["scored"]) if stats["scored"] else "[dim]—[/dim]",
    )
    funnel.add_row(
        "Cover letter", str(stats["with_cover_letter"]), "—",
        _bar(stats["with_cover_letter"], stats["tailored"]) if stats["tailored"] else "[dim]—[/dim]",
    )
    funnel.add_row(
        "Ready to apply", str(stats["ready_to_apply"]), "—",
        "[green]all set[/green]" if stats["ready_to_apply"] > 0 else "[dim]none yet[/dim]",
    )
    funnel.add_row(
        "Applied", str(stats["applied"]), "—",
        f"[dim]{stats['apply_errors']} errors[/dim]" if stats["apply_errors"] else "[dim]—[/dim]",
    )
    console.print(funnel)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # Jobs with tailored resumes + cover letters (ready to apply)
    ready_jobs = conn.execute(
        "SELECT title, site, fit_score, application_url, cover_letter_path "
        "FROM jobs WHERE tailored_resume_path IS NOT NULL "
        "AND cover_letter_path IS NOT NULL "
        "ORDER BY fit_score DESC LIMIT 30"
    ).fetchall()

    if ready_jobs:
        ready_table = Table(
            title="\nFully Processed Jobs (tailored resume + cover letter ready)",
            show_header=True, header_style="bold green",
        )
        ready_table.add_column("#", justify="right", style="dim")
        ready_table.add_column("Title")
        ready_table.add_column("Company")
        ready_table.add_column("Score", justify="center")
        ready_table.add_column("Apply URL")

        for i, row in enumerate(ready_jobs, 1):
            score = row[2]
            score_color = "green" if score >= 7 else "yellow" if score >= 5 else "red"
            url = (row[3] or "—")[:60]
            ready_table.add_row(
                str(i),
                (row[0] or "?")[:40],
                (row[1] or "?")[:20],
                f"[{score_color}]{score}[/{score_color}]",
                url,
            )

        console.print(ready_table)
        console.print(
            "\n  [dim]Run [bold]applypilot export[/bold] to save these to a file "
            "with resume/cover letter paths.[/dim]"
        )

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def export(
    output: Optional[str] = typer.Option(
        None, "--output", "-o",
        help="Output file path. Defaults to ~/.applypilot/ready_to_apply.csv",
    ),
    min_score: int = typer.Option(0, "--min-score", help="Only export jobs with this score or higher."),
) -> None:
    """Export fully processed jobs (tailored resume + cover letter) to a CSV file."""
    _bootstrap()

    import csv
    from applypilot.config import APP_DIR
    from applypilot.database import get_connection

    conn = get_connection()

    query = (
        "SELECT title, site, fit_score, application_url, url, "
        "tailored_resume_path, cover_letter_path, tailored_at "
        "FROM jobs WHERE tailored_resume_path IS NOT NULL "
        "AND cover_letter_path IS NOT NULL"
    )
    params: list = []
    if min_score > 0:
        query += " AND fit_score >= ?"
        params.append(min_score)
    query += " ORDER BY fit_score DESC, tailored_at DESC"

    rows = conn.execute(query, params).fetchall()

    if not rows:
        console.print(
            "[yellow]No fully processed jobs found.[/yellow]\n"
            "Run [bold]applypilot run score tailor cover[/bold] first."
        )
        raise typer.Exit()

    out_path = output or str(APP_DIR / "ready_to_apply.csv")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "Title", "Company", "Score", "Apply URL", "Job URL",
                         "Resume Path", "Cover Letter Path", "Processed At"])
        for i, row in enumerate(rows, 1):
            writer.writerow([i] + list(row))

    # Print summary table
    summary = Table(
        title=f"Exported {len(rows)} jobs → {out_path}",
        show_header=True, header_style="bold green",
    )
    summary.add_column("#", justify="right", style="dim")
    summary.add_column("Title")
    summary.add_column("Company")
    summary.add_column("Score", justify="center")
    summary.add_column("Apply URL")

    for i, row in enumerate(rows[:25], 1):
        score = row[2]
        score_color = "green" if score and score >= 7 else "yellow" if score and score >= 5 else "red"
        url = (row[3] or "—")[:55]
        summary.add_row(
            str(i),
            (row[0] or "?")[:40],
            (row[1] or "?")[:20],
            f"[{score_color}]{score}[/{score_color}]" if score else "—",
            url,
        )

    if len(rows) > 25:
        summary.add_row("...", f"(+{len(rows) - 25} more in file)", "", "", "")

    console.print()
    console.print(summary)
    console.print(f"\n  [bold]Saved to:[/bold] {out_path}")
    console.print(
        "  Open the CSV to see resume and cover letter file paths for each job.\n"
    )


@app.command()
def dashboard() -> None:
    """Generate and open the HTML dashboard in your browser."""
    _bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()


@app.command()
def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    import shutil
    from applypilot.config import (
        load_env, PROFILE_PATH, RESUME_PATH, RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH, get_chrome_path,
    )

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # --- Tier 1 checks ---
    # Profile
    if PROFILE_PATH.exists():
        results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
    else:
        results.append(("profile.json", fail_mark, "Run 'applypilot init' to create"))

    # Resume
    if RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found — plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'applypilot init' to add your resume"))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config — run 'applypilot init'"))

    # jobspy (discovery dep installed separately)
    try:
        import jobspy  # noqa: F401
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except ImportError:
        results.append(("python-jobspy", warn_mark,
                        "pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex"))

    # --- Tier 2 checks ---
    import os
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_local = bool(os.environ.get("LLM_URL"))
    if has_gemini:
        model = os.environ.get("LLM_MODEL", "gemini-3.1-flash-lite")
        results.append(("LLM API key", ok_mark, f"Gemini ({model})"))
    elif has_openai:
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        results.append(("LLM API key", ok_mark, f"OpenAI ({model})"))
    elif has_local:
        results.append(("LLM API key", ok_mark, f"Local: {os.environ.get('LLM_URL')}"))
    else:
        results.append(("LLM API key", fail_mark,
                        "Set GEMINI_API_KEY in ~/.applypilot/.env (run 'applypilot init')"))

    # --- Tier 3 checks (optional — auto-apply disabled) ---
    # Claude Code CLI
    claude_bin = shutil.which("claude")
    if claude_bin:
        results.append(("Claude Code CLI", ok_mark, claude_bin))
    else:
        results.append(("Claude Code CLI", "[dim]optional[/dim]",
                        "Auto-apply disabled — not required"))

    # Chrome
    try:
        chrome_path = get_chrome_path()
        results.append(("Chrome/Chromium", ok_mark, chrome_path))
    except FileNotFoundError:
        results.append(("Chrome/Chromium", "[dim]optional[/dim]",
                        "Auto-apply disabled — not required"))

    # Node.js / npx (for Playwright MCP)
    npx_bin = shutil.which("npx")
    if npx_bin:
        results.append(("Node.js (npx)", ok_mark, npx_bin))
    else:
        results.append(("Node.js (npx)", "[dim]optional[/dim]",
                        "Auto-apply disabled — not required"))

    # CapSolver (optional)
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append(("CapSolver API key", ok_mark, "CAPTCHA solving enabled"))
    else:
        results.append(("CapSolver API key", "[dim]optional[/dim]",
                        "Set CAPSOLVER_API_KEY in .env for CAPTCHA solving"))

    # --- Render results ---
    console.print()
    console.print("[bold]ApplyPilot Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()

    # Tier summary
    from applypilot.config import get_tier, TIER_LABELS
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        console.print("[dim]  → Tier 2 unlocks: scoring, tailoring, cover letters (needs LLM API key)[/dim]")
        console.print("[dim]  → Tier 3 unlocks: auto-apply (needs Claude Code CLI + Chrome + Node.js)[/dim]")
    elif tier == 2:
        console.print("[dim]  → Tier 3 unlocks: auto-apply (needs Claude Code CLI + Chrome + Node.js)[/dim]")

    console.print()


if __name__ == "__main__":
    app()
