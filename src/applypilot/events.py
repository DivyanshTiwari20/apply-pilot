"""Progress event backbone for ApplyPilot.

A tiny, dependency-free publish/subscribe bus. Pipeline code calls :func:`emit`
to announce what it is doing right now; consumers (the CLI logger, the future
web UI's SSE stream) :func:`subscribe` to receive those events.

This exists so that every step of a long run is *visible*. The pipeline emits
fine-grained events ("scoring job 2/5", "writing cover letter", "rate limited,
waiting 10s", "daily quota reached") and any number of listeners can render them
however they like without the pipeline knowing or caring.

Design notes:
  - Thread-safe: discovery/enrichment run in worker threads and the streaming
    pipeline runs stages concurrently, so emit/subscribe are guarded by a lock.
  - Never raises into the caller: a misbehaving listener must not crash the
    pipeline, so listener exceptions are swallowed (logged at debug).
  - Stateless and process-global: fine for the local single-user app. When the
    web layer needs per-run isolation it can filter on ``Event.data["run_id"]``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Event:
    """A single progress event.

    Attributes:
        type: Dotted event name, e.g. ``"stage.start"``, ``"job.scored"``,
            ``"llm.rate_limited"``, ``"quota.exhausted"``.
        message: Human-readable, ready to show to a user.
        data: Structured payload (scores, counts, paths, job titles, ...).
        timestamp: ISO-8601 UTC time the event was emitted.
    """

    type: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON transport (SSE/WebSocket)."""
        return {
            "type": self.type,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }


Listener = Callable[[Event], None]

_listeners: list[Listener] = []
_lock = threading.Lock()


def subscribe(listener: Listener) -> Listener:
    """Register a listener. Returns it so it can be passed to :func:`unsubscribe`."""
    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)
    return listener


def unsubscribe(listener: Listener) -> None:
    """Remove a previously registered listener (no-op if not present)."""
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def emit(type: str, message: str = "", **data: Any) -> Event:
    """Publish an event to all listeners.

    Args:
        type: Dotted event name.
        message: Human-readable description.
        **data: Arbitrary structured payload.

    Returns:
        The :class:`Event` that was published (useful in tests).
    """
    event = Event(type=type, message=message, data=dict(data))
    with _lock:
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener(event)
        except Exception:  # never let a bad listener break the pipeline
            log.debug("Event listener raised for %s", type, exc_info=True)
    return event
