"""Every Reddit request this project makes, through ONE rate budget.

    from reddit_fetch import get, Refused

Why this exists
---------------
Reddit's unauthenticated limit is per ADDRESS, and this project has three
things that want to read Reddit: `reddit_queue.py` (the fic-finder worklist),
`reddit_answers.py` (the answers corpus the extractor is scored against) and
`rec_threads.py` (what a fandom is recommending lately). Each had — or would
have had — its own `time.sleep()` between its own requests, which spaces out
the requests a job makes and does nothing whatever about the requests the other
two are making at the same moment. Three jobs politely pacing themselves into a
shared ceiling is how you get 429s while every one of them believes it is
behaving.

Measured 2026-09-28 from this box, unauthenticated: a request succeeds, a
second 45s later succeeds, a third 40s after that succeeds, and a fourth 40s
later returns 429. So the limit is not a simple fixed gap — it is a bucket that
refills, and the only safe reading is that the gap has to be generous and a 429
has to cost real time rather than be retried into.

The budget therefore lives in `app_settings`, not in a process. A worker loop
and a hand-run script share one address, so they must share one record of when
that address last spoke.

This is not a throughput problem for what it is used for. The recommendation
threads are FORTNIGHTLY: one search plus about two thread fetches per fandom
per month, so fifty fandoms cost ~150 requests a month against the ~700 a day
this pacing allows. A registered app would raise the ceiling to 100/minute and
nothing here would change except MIN_GAP — worth doing for the answers corpus,
which wants thousands of posts, and not needed for recommendations at all.
"""

import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("reddit_fetch")


class Refused(Exception):
    """Reddit would not answer. Not a fault — the expected outcome of asking
    too often, and the caller's job is to stop rather than to retry."""


UA = os.getenv(
    "REDDIT_USER_AGENT",
    "ficatlas/1.0 (fanfiction index; +https://ficatlas.com)")
TIMEOUT = float(os.getenv("REDDIT_TIMEOUT", "20"))

# The floor between any two requests from this address, by anyone.
#
# 300s, and it was 90s until that was measured too. Reddit's unauthenticated
# limit is not a fixed gap between requests — it is a bucket that refills
# slowly, so a pace that survives three or four requests still hits 429 on the
# sixth. Observed from this box:
#
#     45s, 40s, 40s gaps   ->  429 on the fourth request
#     90-115s gaps         ->  429 after a handful
#
# So the gap has to be sized for the SUSTAINED rate, not for the next request.
# At 300s this allows ~288 requests a day, against the ~150 A MONTH that
# fortnightly threads across fifty fandoms actually need — two orders of
# magnitude of headroom, for a job where nothing is latency-sensitive and the
# cost of being refused is a backoff measured in hours. There is no reason to
# bid this down; if throughput ever matters, register an app and set it to 1.
MIN_GAP = float(os.getenv("REDDIT_MIN_GAP", "300"))

# What a 429 costs, doubling each time, so a project that is being refused goes
# quiet instead of hammering. Reset by the first success.
BACKOFF_START = float(os.getenv("REDDIT_BACKOFF_START", "900"))     # 15 min
BACKOFF_MAX = float(os.getenv("REDDIT_BACKOFF_MAX", "21600"))       # 6 h

_LAST = "reddit_last_request_at"
_UNTIL = "reddit_backoff_until"
_STEP = "reddit_backoff_step"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(db, key: str) -> str | None:
    row = db.execute(text("SELECT value FROM app_settings WHERE key=:k"),
                     {"k": key}).first()
    return row[0] if row else None


def _write(db, key: str, value: str) -> None:
    db.execute(text("""
        INSERT INTO app_settings (key, value) VALUES (:k, :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """), {"k": key, "v": value})


def _parse(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def budget_state() -> dict:
    """What the shared budget currently says. For the admin panel and for
    deciding whether a pass is worth starting at all."""
    with db_session() as db:
        until = _parse(_read(db, _UNTIL))
        last = _parse(_read(db, _LAST))
        step = float(_read(db, _STEP) or 0)
    now = _now()
    return {
        "last_request_at": last.isoformat() if last else None,
        "backoff_until": until.isoformat() if until else None,
        "backed_off": bool(until and until > now),
        "backoff_seconds_left": int((until - now).total_seconds()) if until and until > now else 0,
        "backoff_step": step,
    }


def _claim_slot() -> float:
    """Reserve the next request slot, returning how long to wait for it.

    Claimed BEFORE the request rather than recorded after it, and that ordering
    is the point: two processes that both read "last request was 5 minutes ago"
    and then both fetch have each behaved correctly and together broken the
    limit. Writing the new timestamp inside the same transaction that reads it
    means the second one sees the first one's claim.
    """
    with db_session() as db:
        # Serialise the claim itself; the row is tiny and held for microseconds.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('reddit_budget'))"))
        now = _now()
        until = _parse(_read(db, _UNTIL))
        if until and until > now:
            raise Refused(f"backed off for another {int((until - now).total_seconds())}s")
        last = _parse(_read(db, _LAST))
        wait = 0.0
        if last:
            elapsed = (now - last).total_seconds()
            if elapsed < MIN_GAP:
                wait = MIN_GAP - elapsed
        # Jitter, so two jobs released by the same clock tick do not line up.
        wait += random.uniform(0, 3)
        _write(db, _LAST, (now + timedelta(seconds=wait)).isoformat())
        db.commit()
    return wait


def _penalise() -> None:
    with db_session() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('reddit_budget'))"))
        step = float(_read(db, _STEP) or 0)
        step = BACKOFF_START if step <= 0 else min(step * 2, BACKOFF_MAX)
        _write(db, _STEP, str(step))
        _write(db, _UNTIL, (_now() + timedelta(seconds=step)).isoformat())
        db.commit()
    log.warning("reddit refused us; quiet for %.0f minutes", step / 60)


def _forgive() -> None:
    with db_session() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('reddit_budget'))"))
        if _read(db, _STEP) not in (None, "0"):
            _write(db, _STEP, "0")
            _write(db, _UNTIL, "")
        db.commit()


def get(url: str) -> str:
    """Fetch through the shared budget. Raises Refused rather than retrying.

    Retrying a 429 is what turns a rate limit into a ban, so there is no retry
    here at all: the caller stops, the backoff records why, and the next pass
    picks up where this one left off. Every consumer of this module is a
    resumable worklist for exactly that reason.
    """
    wait = _claim_slot()
    if wait > 0:
        log.info("reddit: waiting %.0fs for the shared budget", wait)
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
        _forgive()
        return body
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            _penalise()
            raise Refused(f"HTTP {e.code}") from e
        # A real answer: gone, private, or quarantined. Not a budget problem,
        # so it must not cost the other jobs their throughput.
        log.info("reddit: HTTP %s on %s", e.code, url)
        raise Refused(f"HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001 — a pass must never die here
        raise Refused(type(e).__name__) from e
