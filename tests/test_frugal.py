"""Tests for the depth-first (frugal) orchestrator in applypilot.pipeline.

The key guarantee: jobs are finished one at a time, so when the quota runs out
mid-run the already-finished jobs are complete and saved. No real API or DB.
"""

import pytest

import applypilot.config as cfg
import applypilot.database as db
import applypilot.pipeline as pipe
import applypilot.scoring.cover_letter as cover_mod
import applypilot.scoring.scorer as scorer_mod
import applypilot.scoring.tailor as tailor_mod
from applypilot.llm import QuotaExhausted


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    """Stub out I/O so _run_per_job runs purely in-memory, and record calls."""
    resume = tmp_path / "resume.txt"
    resume.write_text("RESUME TEXT", encoding="utf-8")

    monkeypatch.setattr(cfg, "RESUME_PATH", resume)
    monkeypatch.setattr(cfg, "load_profile", lambda: {})
    monkeypatch.setattr(pipe, "get_connection", lambda *a, **k: object())
    monkeypatch.setattr(pipe, "_save_score", lambda *a, **k: None)

    calls: list[tuple[str, str]] = []

    def fake_save_tailored(conn, job, tailored, report, commit=True):
        calls.append(("tailor", job["url"]))
        return {"url": job["url"], "path": f"{job['url']}.txt",
                "pdf_path": f"{job['url']}.pdf", "title": job["title"],
                "site": job["site"], "status": report["status"], "attempts": 1}

    def fake_save_cover(conn, job, letter, commit=True):
        calls.append(("cover", job["url"]))
        return {"url": job["url"], "path": f"{job['url']}_CL.txt",
                "pdf_path": f"{job['url']}_CL.pdf", "title": job["title"],
                "site": job["site"]}

    monkeypatch.setattr(tailor_mod, "save_tailored_result", fake_save_tailored)
    monkeypatch.setattr(cover_mod, "save_cover_result", fake_save_cover)
    monkeypatch.setattr(cover_mod, "generate_cover_letter",
                        lambda *a, **k: "Dear Hiring Manager, ...")

    return calls


def _jobs(*scores):
    return [
        {"url": f"u{i}", "title": f"Job {i}", "site": "Co",
         "fit_score": s, "full_description": "desc"}
        for i, s in enumerate(scores, 1)
    ]


def test_depth_first_finishes_first_job_before_quota_hits_second(stub_pipeline, monkeypatch):
    calls = stub_pipeline
    jobs = _jobs(9, 9)  # both already scored high
    monkeypatch.setattr(db, "get_jobs_by_stage", lambda **k: jobs)

    def fake_tailor(resume, job, profile, validation_mode="normal"):
        if job["url"] == "u2":
            raise QuotaExhausted("daily quota gone", reason="daily")
        return ("tailored text", {"status": "approved", "attempts": 1})

    monkeypatch.setattr(tailor_mod, "tailor_resume", fake_tailor)

    result = pipe._run_per_job(["score", "tailor", "cover"], min_score=7,
                               validation_mode="lenient", max_jobs=5)

    # Job 1 fully done (tailored + cover), job 2 stopped before any save.
    assert ("tailor", "u1") in calls
    assert ("cover", "u1") in calls
    assert ("tailor", "u2") not in calls
    assert ("cover", "u2") not in calls
    assert result["completed"] == 1
    assert result["stopped"][0] == "daily"


def test_below_min_score_is_skipped_not_tailored(stub_pipeline, monkeypatch):
    calls = stub_pipeline
    jobs = _jobs(3, 8)  # first below threshold, second above
    monkeypatch.setattr(db, "get_jobs_by_stage", lambda **k: jobs)
    monkeypatch.setattr(tailor_mod, "tailor_resume",
                        lambda *a, **k: ("text", {"status": "approved", "attempts": 1}))

    result = pipe._run_per_job(["score", "tailor", "cover"], min_score=7,
                               validation_mode="lenient", max_jobs=5)

    assert ("tailor", "u1") not in calls   # skipped
    assert ("tailor", "u2") in calls       # processed
    assert result["completed"] == 1


def test_scores_unscored_job_then_processes(stub_pipeline, monkeypatch):
    calls = stub_pipeline
    jobs = _jobs(None)  # not yet scored
    monkeypatch.setattr(db, "get_jobs_by_stage", lambda **k: jobs)
    monkeypatch.setattr(scorer_mod, "score_job",
                        lambda resume, job: {"score": 8, "keywords": "k", "reasoning": "r"})
    monkeypatch.setattr(tailor_mod, "tailor_resume",
                        lambda *a, **k: ("text", {"status": "approved", "attempts": 1}))

    result = pipe._run_per_job(["score", "tailor", "cover"], min_score=7,
                               validation_mode="lenient", max_jobs=5)

    assert result["completed"] == 1
    assert ("tailor", "u1") in calls
