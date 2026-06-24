"""Background pipeline runner + live event broadcasting for the web app.

The pipeline already emits fine-grained progress on the :mod:`applypilot.events`
bus. This module:
  - fans those events out to every connected browser (Server-Sent Events), and
  - runs the pipeline in a background thread so the HTTP request returns
    immediately while work streams live to the UI.

Single-user/local: at most one run at a time.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections import deque
from typing import Any

from applypilot import events

log = logging.getLogger(__name__)

# The pipeline prints rich console output with Unicode (arrows, checks). On
# Windows the server's stdout is often cp1252, where those characters raise
# UnicodeEncodeError and would crash the whole run. Degrade to '?' instead.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# Ring buffer of recent events so a browser that connects mid-run still sees
# the recent history (not just events from this moment on).
_history: deque[dict] = deque(maxlen=300)

# Active SSE subscriber queues (one per open browser tab).
_subscribers: set[queue.Queue] = set()
_sub_lock = threading.Lock()

# Current run state.
_run_lock = threading.Lock()
_run_thread: threading.Thread | None = None
_run_state: dict[str, Any] = {
    "status": "idle",      # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "params": None,
    "error": None,
    "result": None,
}


def _broadcast(event) -> None:
    """Event-bus listener: push every event to history and all subscribers."""
    payload = event.to_dict()
    _history.append(payload)
    with _sub_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # a stuck/slow client must not block the pipeline


# Register the fan-out listener exactly once at import.
events.subscribe(_broadcast)


# ── SSE subscriber management ─────────────────────────────────────────────

def add_subscriber() -> queue.Queue:
    """Register a new SSE client; returns its event queue."""
    q: queue.Queue = queue.Queue(maxsize=1000)
    with _sub_lock:
        _subscribers.add(q)
    return q


def remove_subscriber(q: queue.Queue) -> None:
    with _sub_lock:
        _subscribers.discard(q)


def recent_events() -> list[dict]:
    return list(_history)


# ── Run state ─────────────────────────────────────────────────────────────

def get_state() -> dict:
    with _run_lock:
        return dict(_run_state)


def is_running() -> bool:
    with _run_lock:
        return _run_state["status"] == "running"


def request_stop() -> dict:
    """Ask the active run to stop at the next LLM call / job boundary."""
    from applypilot.llm import request_cancel
    if not is_running():
        return {"ok": False, "error": "No run is in progress."}
    request_cancel()
    events.emit("run.stopping", "Stop requested — finishing the current step…")
    return {"ok": True}


def start_run(params: dict) -> dict:
    """Start a pipeline run in the background. Returns {ok, error?}."""
    from applypilot.llm import clear_cancel
    global _run_thread
    with _run_lock:
        if _run_state["status"] == "running":
            return {"ok": False, "error": "A run is already in progress."}
        _run_state.update(
            status="running", started_at=time.time(), finished_at=None,
            error=None, result=None, params=params,
        )
    clear_cancel()

    def _worker() -> None:
        from applypilot.pipeline import run_pipeline
        try:
            result = run_pipeline(
                stages=params.get("stages") or ["all"],
                min_score=params.get("min_score", 7),
                max_jobs=params.get("max_jobs", 0),
                validation_mode=params.get("validation", "normal"),
                workers=params.get("workers", 1),
                frugal=params.get("frugal"),
            )
            with _run_lock:
                _run_state.update(
                    status="done", finished_at=time.time(),
                    result={
                        "errors": list(result.get("errors", {}).keys()),
                        "frugal": result.get("frugal"),
                        "calls": result.get("calls"),
                    },
                )
        except Exception as e:  # surface, don't crash the server
            log.exception("Web run failed")
            with _run_lock:
                _run_state.update(status="error", finished_at=time.time(), error=str(e))
            events.emit("run.error", f"Run failed: {e}", error=str(e))

    def _heartbeat(start_ts: float) -> None:
        # Keep the UI alive during long single calls (some models take ~1 min),
        # so a slow step never looks frozen.
        while True:
            time.sleep(8)
            with _run_lock:
                if _run_state["status"] != "running":
                    return
            events.emit("run.tick", "", elapsed=round(time.time() - start_ts))

    started = time.time()
    _run_thread = threading.Thread(target=_worker, name="webapp-run", daemon=True)
    _run_thread.start()
    threading.Thread(target=_heartbeat, args=(started,), daemon=True).start()
    return {"ok": True}
