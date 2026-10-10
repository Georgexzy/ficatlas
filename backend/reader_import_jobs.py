"""Database-backed status for reader-initiated full-text imports.

The import itself runs after the POST has returned, so Next/proxy timeouts do
not turn a slow archive download into a false failure. State is in app_settings
rather than the API process's memory: public API requests are served by multiple
workers, and the POST and its polls may reach different ones.
"""
from __future__ import annotations

import json
import secrets
import time

from sqlalchemy import text

PREFIX = "reader_import_job:"
RETENTION_S = 24 * 60 * 60


def _ensure(db) -> None:
    from api.settings import _ensure_table
    _ensure_table(db)


def create(db, user_id: str) -> tuple[str, dict]:
    _ensure(db)
    job_id = secrets.token_urlsafe(18)
    state = {
        "id": job_id, "user_id": str(user_id), "status": "running",
        "progress": "Fetching and preparing the story…", "started_at": time.time(),
    }
    db.execute(text("INSERT INTO app_settings (key,value) VALUES (:k,:v)"), {
        "k": PREFIX + job_id, "v": json.dumps(state),
    })
    # Keep the namespace bounded without a schema change or a separate worker.
    for key, value in db.execute(text(
            "SELECT key,value FROM app_settings WHERE key LIKE :p"),
            {"p": PREFIX + "%"}).all():
        try:
            old = json.loads(value)
            if old.get("status") != "running" and time.time() - float(old.get("started_at", 0)) > RETENTION_S:
                db.execute(text("DELETE FROM app_settings WHERE key=:k"), {"k": key})
        except (ValueError, TypeError):
            db.execute(text("DELETE FROM app_settings WHERE key=:k"), {"k": key})
    db.commit()
    return job_id, state


def update(db, job_id: str, **fields) -> dict | None:
    _ensure(db)
    key = PREFIX + job_id
    row = db.execute(text("SELECT value FROM app_settings WHERE key=:k"),
                     {"k": key}).first()
    if not row:
        return None
    try:
        state = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    state.update(fields)
    db.execute(text("UPDATE app_settings SET value=:v WHERE key=:k"),
               {"k": key, "v": json.dumps(state)})
    db.commit()
    return state


def read(db, job_id: str, user_id: str) -> dict | None:
    _ensure(db)
    row = db.execute(text("SELECT value FROM app_settings WHERE key=:k"),
                     {"k": PREFIX + job_id}).first()
    if not row:
        return None
    try:
        state = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    if state.get("user_id") != str(user_id):
        return None
    return {k: v for k, v in state.items() if k != "user_id"}
