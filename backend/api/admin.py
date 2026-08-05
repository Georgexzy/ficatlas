"""
Operator view of the index: what is thin, what is running, what is throttled.
============================================================================

Everything here was previously only knowable by reading container logs or
opening psql. That is fine when the only user is the person who built it and is
already in a terminal; it stops being fine the moment the site is public and the
question becomes "is this healthy" rather than "what did I just break".

Two things it deliberately does NOT do:

  * no live counts on the big table. Every figure comes from reltuples or a
    bounded query — a COUNT(*) FILTER over 19.7M rows takes ~10s per column and
    an admin page that costs a minute of database time is one nobody opens.
  * no destructive actions. This reports and it starts background passes; the
    things that change what readers see live in Settings and the takedown queue,
    where they are individually explained.
"""

import logging
import os
import time

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from db.session import get_db
from models.user import User
from api.auth import require_admin
from api.settings import get_setting, put_setting

log = logging.getLogger(__name__)
router = APIRouter()

# Sampled rather than counted. An exact answer to "how many AO3 rows are stubs"
# means a filtered scan of 13.1M rows; a sample answers it to within a fraction
# of a percent. The page says it is a sample.
#
# 50k rather than the 200k I started with: under a bulk import the same query
# went from instant to 66 SECONDS, which Next's proxy turned into a 30s timeout
# and a 500. An operator page has to stay usable exactly when the box is busy,
# because that is when someone opens it.
_SAMPLE = 50_000

_COVERAGE = sql_text(f"""
    WITH s AS (
        SELECT site, word_count, kudos, characters, relationships,
               genres, summary, published_at
        FROM stories TABLESAMPLE SYSTEM_ROWS(:n)
    )
    SELECT site,
           count(*)                                                        AS sampled,
           count(*) FILTER (WHERE COALESCE(word_count,0) = 0)              AS no_words,
           count(*) FILTER (WHERE COALESCE(kudos,0) = 0)                   AS no_kudos,
           count(*) FILTER (WHERE characters    IS NULL OR characters    = '{{}}') AS no_chars,
           count(*) FILTER (WHERE relationships IS NULL OR relationships = '{{}}') AS no_ships,
           count(*) FILTER (WHERE genres        IS NULL OR genres        = '{{}}') AS no_genres,
           count(*) FILTER (WHERE nullif(summary,'') IS NULL)              AS no_summary,
           count(*) FILTER (WHERE published_at IS NULL)                    AS no_date
    FROM s GROUP BY site
""")


# Served from cache and refreshed on demand. Two reasons, both learned the hard
# way: the figures move slowly enough that a three-minute-old answer is the same
# answer, and under write load the underlying queries are slow enough to time
# out at the proxy. Stale-but-instant beats fresh-but-500.
_CACHE: dict | None = None
_CACHED_AT = 0.0
_TTL = 180.0


@router.get("/overview")
async def overview(refresh: bool = False,
                   db: Session = Depends(get_db),
                   _admin: User = Depends(require_admin)):
    """Index health, crawl configuration and rate-limit state in one request."""
    global _CACHE, _CACHED_AT
    if _CACHE is not None and not refresh and (time.time() - _CACHED_AT) < _TTL:
        return {**_CACHE, "cached": True,
                "age_s": int(time.time() - _CACHED_AT)}
    out: dict = {}

    # Row counts from the planner's own estimate. Accurate to a percent or so on
    # a table that is analysed regularly, and free.
    rows = db.execute(sql_text("""
        SELECT relname, reltuples::bigint
        FROM pg_class WHERE relname IN ('stories','chapters','facets','takedowns')
    """)).fetchall()
    out["tables"] = {r[0]: max(0, int(r[1])) for r in rows}

    try:
        cov = db.execute(_COVERAGE, {"n": _SAMPLE}).fetchall()
        out["coverage"] = [{
            "site": r[0], "sampled": r[1],
            "no_words": r[2], "no_kudos": r[3], "no_chars": r[4],
            "no_ships": r[5], "no_genres": r[6], "no_summary": r[7], "no_date": r[8],
        } for r in cov]
        out["coverage_sample"] = _SAMPLE
    except Exception as e:
        # tsm_system_rows is an extension; without it the page still renders.
        log.info(f"coverage sample unavailable: {type(e).__name__}")
        out["coverage"] = []

    # What the recent-works crawler is currently pointed at.
    pool = int(get_setting(db, "crawl_rotate_pool") or 250)
    cursor = int(get_setting(db, "crawl_rotate_cursor") or 0)
    per_pass = int(get_setting(db, "crawl_rotate_count") or 3)
    names = [r[0] for r in db.execute(sql_text(
        "SELECT value FROM facets WHERE kind='fandom_ao3' ORDER BY count DESC LIMIT :p"
    ), {"p": pool}).fetchall()]
    upcoming = [names[(cursor + i) % len(names)] for i in range(min(per_pass, len(names)))] if names else []
    interval_min = float(os.getenv("RECENT_INTERVAL_MIN", "20"))
    out["crawl"] = {
        "mode": get_setting(db, "crawl_mode") or "mixed",
        "pinned": [f.strip() for f in (get_setting(db, "tracked_fandom") or "").split(",") if f.strip()],
        "rotate_count": per_pass,
        "pool": len(names),
        "cursor": cursor,
        "upcoming": upcoming,
        # How long one full sweep of the pool takes at the current settings —
        # the number that actually says whether the rotation is meaningful.
        "cycle_hours": round((len(names) / per_pass) * interval_min / 60, 1) if per_pass and names else None,
        "direct_crawl": (get_setting(db, "enable_direct_crawl") or "false") == "true",
        "disabled": {
            "ao3": (get_setting(db, "crawl_disabled_ao3") or "") == "true",
            "ffnet": (get_setting(db, "crawl_disabled_ffnet") or "") == "true",
        },
    }

    # Shared rate-limit state, so a slow crawl has a visible reason.
    try:
        budget = db.execute(sql_text(
            "SELECT host, interval_s, GREATEST(0, EXTRACT(EPOCH FROM (next_at - now()))) "
            "FROM crawl_budget ORDER BY host")).fetchall()
        out["budget"] = [{"host": b[0], "interval_s": round(float(b[1]), 1),
                          "queued_s": round(float(b[2]), 1)} for b in budget]
    except Exception:
        out["budget"] = []

    # takedowns is tiny, so an exact count is free. The two on `stories` are
    # bounded by their partial indexes and still felt it under load, so they get
    # a statement timeout and fall back rather than hanging the whole page.
    out["takedowns_pending"] = db.execute(sql_text(
        "SELECT count(*) FROM takedowns WHERE state='pending'")).scalar() or 0
    for key, where in (("hosted_public", "is_hosted"),
                       ("delisted", "delisted_at IS NOT NULL")):
        try:
            db.execute(sql_text("SET LOCAL statement_timeout = '4s'"))
            out[key] = db.execute(sql_text(
                f"SELECT count(*) FROM stories WHERE {where}")).scalar() or 0
        except Exception:
            db.rollback()
            out[key] = None            # rendered as "—" rather than a wrong 0

    _CACHE, _CACHED_AT = out, time.time()
    return {**out, "cached": False, "age_s": 0}


# Background passes an operator can start by hand. Each is already on a timer;
# these exist for "I have just fixed the thing that was blocking it and do not
# want to wait 30 minutes to find out whether it worked".
_JOBS = {
    "ao3_stubs": ("ao3_stub_enrich", "Fill AO3 rows indexed as bare titles"),
    "ffnet_meta": ("ffnet_enrich", "Backfill FF.net characters and genres from Wayback"),
}

_running: dict[str, float] = {}


@router.post("/run/{job}")
async def run_job(job: str, limit: int = Form(200),
                  db: Session = Depends(get_db),
                  _admin: User = Depends(require_admin)):
    """Start one enrichment pass in the background.

    Deliberately fire-and-forget with a single-flight guard rather than a job
    queue: these are idempotent passes that pick their own targets, so the worst
    a duplicate does is waste requests against a rate limiter that is already
    shared. A queue would be more machinery than the problem has.
    """
    if job not in _JOBS:
        raise HTTPException(404, f"No such job. Known: {', '.join(sorted(_JOBS))}")
    last = _running.get(job, 0.0)
    if time.time() - last < 60:
        raise HTTPException(429, "That pass was started less than a minute ago.")
    _running[job] = time.time()

    import asyncio
    module, _desc = _JOBS[job]

    def _go():
        try:
            if job == "ao3_stubs":
                import ao3_stub_enrich
                asyncio.run(ao3_stub_enrich.enrich(min(limit, 2000), False))
            else:
                from ffnet_enrich import run as enrich_run
                enrich_run(min(limit, 2000), False, 0.0, 25, 600.0)
        except Exception as e:
            log.warning(f"admin job {job} failed: {type(e).__name__}: {e}")

    asyncio.get_event_loop().run_in_executor(None, _go)
    return {"started": job, "limit": min(limit, 2000)}
