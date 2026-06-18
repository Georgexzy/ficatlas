"""Tiny in-memory job store for async discovery scrapes.

We need this because:
  - AO3 deep scrapes can take 10-60s (multiple pages × 3s polite delays + AO3 latency)
  - A synchronous HTTP request that long is fragile through any proxy chain
    (Tailscale → Next.js rewrite → backend; any of these can drop it)
  - Browsers may also have their own intermediate timeouts on hot reload, tab focus, etc.

Pattern:
    Start:    POST /discover-ao3       → returns {job_id}, scrape runs as a task
    Progress: GET  /library/jobs/{id}  → returns {status, progress, found, ...}
    Done:     final poll returns       → {status: "done", found, newly_indexed, ...}

Single-process, single-worker scope is fine for this app (1 user, hobby scale).
If we ever multi-process, swap this for Redis or DB-backed jobs.
"""
import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine

# job_id -> dict with at minimum {status: "running"|"done"|"error", progress: str}
_jobs: dict[str, dict] = {}

# Time after which finished jobs are evicted, so the store doesn't grow forever
_RETAIN_FINISHED = timedelta(minutes=15)


def new_job(kind: str) -> tuple[str, dict]:
    """Create a new job record and return (job_id, state_dict)."""
    job_id = secrets.token_urlsafe(12)
    state: dict[str, Any] = {
        "id":          job_id,
        "kind":        kind,
        "status":      "running",   # running | done | error
        "progress":    "Starting…",
        "pages_ok":    0,
        "pages_failed": 0,
        "found":       0,
        "newly_indexed": 0,
        "error":       None,
        "started_at":  datetime.utcnow().isoformat(),
        "finished_at": None,
    }
    _jobs[job_id] = state
    _evict_old()
    return job_id, state


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def _evict_old() -> None:
    """Drop finished jobs older than _RETAIN_FINISHED to bound memory."""
    cutoff = datetime.utcnow() - _RETAIN_FINISHED
    drop: list[str] = []
    for jid, st in _jobs.items():
        if st["status"] in ("done", "error") and st.get("finished_at"):
            try:
                if datetime.fromisoformat(st["finished_at"]) < cutoff:
                    drop.append(jid)
            except Exception:
                pass
    for jid in drop:
        _jobs.pop(jid, None)


def run_in_background(coro_fn: Callable[[], Coroutine]) -> None:
    """Schedule a coroutine as a fire-and-forget task that won't be GC'd mid-run."""
    asyncio.create_task(coro_fn())
