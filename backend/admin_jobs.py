"""The admin panel's pass runner: requested by the API, run by the WORKER.

Why this is not a background task in the API any more
------------------------------------------------------
The first version ran each pass in the API process that received the click and
kept its state in that process's memory. Two faults followed directly from where
it ran, neither visible in the diff:

* **The readout was per process, and there are several processes.** The dev
  backend runs WEB_CONCURRENCY=4 and the public api 2, and each poll lands on
  whichever worker the socket happens to reach. So 3 polls in 4 (dev) or 1 in 2
  (public) found no record of the pass and the panel said "lost track of it —
  the worker restarted" about a pass that was running fine — while the
  single-flight guard, in the same memory, let a second click on another worker
  start a duplicate.
* **The API is the container that gets restarted.** A promote stops the old api
  colour 120 seconds after the switch, and the dev backend is restarted for any
  code change. `popularity_rank` takes 3h51m; started from the panel it would
  almost certainly die mid-pass. CLAUDE.md already says this about the gate
  backfill — "Run it on the WORKER, not the backend" — and the button quietly
  did the opposite.

So the API now only WRITES A REQUEST, into `app_settings` (shared by every
process, the public tier included, because the database is shared), and the
worker's `_admin_jobs_loop` claims and runs it. State lives in one row per pass,
readable from any process. The worker stamps a heartbeat so the panel can tell
"queued, worker is coming" from "queued, and nothing is going to pick it up".

Every value is JSON in a TEXT column. The keys are namespaced (`admin_job:…`) and
are NOT in `api.settings.DEFAULTS`, so the public settings endpoint can neither
write nor read them.
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import text

PREFIX = "admin_job:"
HEARTBEAT_KEY = "admin_jobs_worker_seen"
LOG_KEY = "admin_job_log"
LOG_KEEP = 25
# How long a heartbeat may be silent before the panel says nothing is listening.
# The loop ticks every POLL_S; three missed ticks is not a hiccup.
POLL_S = 10
WORKER_SILENT_S = POLL_S * 6

ACTIVE = ("queued", "running")


def _now() -> float:
    return time.time()


def _ensure(db) -> None:
    from api.settings import _ensure_table
    _ensure_table(db)


def read(db, key: str) -> dict | None:
    _ensure(db)
    row = db.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                     {"k": PREFIX + key}).first()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def read_all(db) -> dict[str, dict]:
    _ensure(db)
    out: dict[str, dict] = {}
    for k, v in db.execute(text(
            "SELECT key, value FROM app_settings WHERE key LIKE :p"),
            {"p": PREFIX + "%"}):
        try:
            out[k[len(PREFIX):]] = json.loads(v)
        except ValueError:
            continue
    return out


def worker_seen_s(db) -> float | None:
    """Seconds since the worker's job loop last looked, or None if it never has."""
    _ensure(db)
    row = db.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                     {"k": HEARTBEAT_KEY}).first()
    try:
        return max(0.0, _now() - float(row[0])) if row else None
    except (TypeError, ValueError):
        return None


def history(db) -> list[dict]:
    _ensure(db)
    row = db.execute(text("SELECT value FROM app_settings WHERE key = :k"),
                     {"k": LOG_KEY}).first()
    try:
        return json.loads(row[0]) if row else []
    except ValueError:
        return []


class Busy(Exception):
    """The pass is already queued or running."""


def request(db, key: str, limit: int, dry_run: bool) -> dict:
    """Queue one run. Single-flight across EVERY process, enforced in SQL.

    The upsert only overwrites a row whose state is not active, so two clicks on
    two API workers cannot both queue — the second gets no row back and is told
    so. That is the guarantee the in-memory `_running` dict claimed and, with
    more than one worker, never had.
    """
    _ensure(db)
    rec = {"job": key, "limit": limit, "dry_run": dry_run, "state": "queued",
           "requested_at": _now()}
    got = db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
         WHERE (app_settings.value::jsonb ->> 'state') IS DISTINCT FROM 'queued'
           AND (app_settings.value::jsonb ->> 'state') IS DISTINCT FROM 'running'
        RETURNING key
    """), {"k": PREFIX + key, "v": json.dumps(rec)}).first()
    db.commit()
    if not got:
        raise Busy(key)
    return rec


def cancel(db, key: str) -> bool:
    """Withdraw a QUEUED request. A running pass cannot be stopped from here —
    it is a thread inside the worker, and Python has no safe way to kill one."""
    cur = read(db, key)
    if not cur or cur.get("state") != "queued":
        return False
    new = {**cur, "state": "cancelled", "finished_at": _now()}
    ok = _swap(db, key, cur, new)
    if ok:
        _append_log(db, new)
    return ok


def _swap(db, key: str, old: dict, new: dict) -> bool:
    """Compare-and-set on the row's text, so a claim races nothing."""
    got = db.execute(text("""
        UPDATE app_settings SET value = :new WHERE key = :k AND value = :old
        RETURNING key
    """), {"k": PREFIX + key, "old": json.dumps(old), "new": json.dumps(new)}).first()
    db.commit()
    return bool(got)


def claim_queued(db) -> list[dict]:
    """Move every queued request to running and return them. Worker only."""
    claimed = []
    for key, rec in read_all(db).items():
        if rec.get("state") != "queued":
            continue
        new = {**rec, "state": "running", "started_at": _now()}
        if _swap(db, key, rec, new):
            claimed.append(new)
    return claimed


def finish(db, rec: dict, *, report: str | None = None,
           error: str | None = None, deferred: bool = False) -> dict:
    out = {**rec, "finished_at": _now()}
    if deferred:
        out["state"] = "deferred"
        out["error"] = error
    elif error is not None:
        out["state"] = "failed"
        out["error"] = error
    else:
        out["state"] = "done"
        out["report"] = report or "finished"
    _ensure(db)
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """), {"k": PREFIX + rec["job"], "v": json.dumps(out)})
    db.commit()
    _append_log(db, out)
    return out


def abandon_running(db) -> int:
    """At worker start: anything still `running` died with the last worker.

    Without this a pass interrupted by a restart reads `running` for ever, and
    the single-flight guard refuses to start it again — the button would be
    dead until somebody edited the row by hand.
    """
    n = 0
    for key, rec in read_all(db).items():
        if rec.get("state") == "running":
            finish(db, rec, error="the worker restarted while this was running")
            n += 1
    return n


def heartbeat(db) -> None:
    _ensure(db)
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """), {"k": HEARTBEAT_KEY, "v": f"{_now():.0f}"})
    db.commit()


def _append_log(db, rec: dict) -> None:
    """Newest first, bounded. Row-locked so two finishing passes do not drop
    each other's entry."""
    _ensure(db)
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, '[]')
        ON CONFLICT (key) DO NOTHING
    """), {"k": LOG_KEY})
    row = db.execute(text("SELECT value FROM app_settings WHERE key = :k FOR UPDATE"),
                     {"k": LOG_KEY}).first()
    try:
        log: list[Any] = json.loads(row[0]) if row else []
    except ValueError:
        log = []
    log.insert(0, rec)
    db.execute(text("UPDATE app_settings SET value = :v WHERE key = :k"),
               {"k": LOG_KEY, "v": json.dumps(log[:LOG_KEEP])})
    db.commit()
