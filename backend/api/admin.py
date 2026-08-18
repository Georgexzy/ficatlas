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

_FIELDS = {
    "no_words":   "COALESCE(word_count,0) = 0",
    "no_kudos":   "COALESCE(kudos,0) = 0",
    "no_chars":   "characters    IS NULL OR characters    = '{}'",
    "no_ships":   "relationships IS NULL OR relationships = '{}'",
    "no_genres":  "genres        IS NULL OR genres        = '{}'",
    "no_summary": "nullif(summary,'') IS NULL",
    "no_date":    "published_at IS NULL",
}

# Which fields a site actually publishes.
#
# Reporting AO3 as "100% missing genres" was not a gap, it was a category error:
# genres is FanFiction.net's vocabulary and AO3 has literally zero rows with one
# (checked, not assumed). A red bar there says "fix this", and there is nothing
# to fix — which devalues the bars that DO mean something.
_NOT_APPLICABLE = {
    "ao3": {"no_genres"},
    "fictionalley": {"no_kudos"},   # FictionAlley had no kudos concept
}

# Below this many rows a site is counted exactly instead of sampled.
#
# The sample is drawn per BLOCK, and rows are clustered on disk by import batch,
# so a small site lands in a handful of blocks and gets a handful of rows. In
# practice FictionAlley drew 61–115 rows out of 50,000 and reported 21%, 23% and
# 34% for a field whose true value is 18.5% — presented with exactly the same
# confidence as AO3's 33,000-row estimate. Anything this small is cheap to count
# properly: the exact FictionAlley figures come back in under a second.
EXACT_BELOW = 400_000


def _site_totals(db: Session) -> dict[str, int]:
    """Rows per site, from the planner rather than a scan."""
    rows = db.execute(sql_text("""
        SELECT site, count(*) FROM stories
        GROUP BY site
    """)).fetchall()
    return {(r[0].value if hasattr(r[0], "value") else str(r[0])): int(r[1]) for r in rows}



# Served from cache and refreshed on demand. Two reasons, both learned the hard
# way: the figures move slowly enough that a three-minute-old answer is the same
# answer, and under write load the underlying queries are slow enough to time
# out at the proxy. Stale-but-instant beats fresh-but-500.
_CACHE: dict | None = None
_CACHED_AT = 0.0
_TTL = 180.0


def _coverage(db: Session) -> list[dict]:
    """Per-site field coverage: exact for small sites, sampled for large ones."""
    sel = ", ".join(
        f"count(*) FILTER (WHERE {cond}) AS {name}" for name, cond in _FIELDS.items())

    try:
        totals = _site_totals(db)
    except Exception as e:
        log.info(f"site totals unavailable: {type(e).__name__}")
        return []

    out: list[dict] = []
    big = [s for s, n in totals.items() if n >= EXACT_BELOW]

    # Small sites: counted properly. Each is a bounded index scan.
    for site, n in totals.items():
        if n >= EXACT_BELOW:
            continue
        try:
            r = db.execute(sql_text(
                f"SELECT count(*) AS sampled, {sel} FROM stories WHERE site = :s"),
                {"s": site}).first()
            out.append({"site": site, "sampled": int(r[0]), "exact": True,
                        **{name: int(r[i + 1]) for i, name in enumerate(_FIELDS)}})
        except Exception as e:
            log.info(f"exact coverage for {site} failed: {type(e).__name__}")

    # Large sites: sampled, and big enough that a block sample is representative.
    if big:
        try:
            rows = db.execute(sql_text(f"""
                WITH s AS (SELECT * FROM stories TABLESAMPLE SYSTEM_ROWS(:n))
                SELECT site, count(*) AS sampled, {sel} FROM s
                WHERE site = ANY(:sites) GROUP BY site
            """), {"n": _SAMPLE, "sites": big}).fetchall()
            for r in rows:
                site = r[0].value if hasattr(r[0], "value") else str(r[0])
                out.append({"site": site, "sampled": int(r[1]), "exact": False,
                            **{name: int(r[i + 2]) for i, name in enumerate(_FIELDS)}})
        except Exception as e:
            log.info(f"coverage sample unavailable: {type(e).__name__}")

    for c in out:
        c["total"] = totals.get(c["site"], 0)
        c["na"] = sorted(_NOT_APPLICABLE.get(c["site"], set()))

    # Which route can actually close each site's gaps — the part that turns this
    # panel from a list of complaints into something you can act on.
    #
    # For AO3 the answer is mostly "the listing harvest, already running": it
    # gets the same fields twenty works per request by walking fandom tag pages.
    # The exception is the pocket of rows with NO fandom, which a tag-page walk
    # cannot see by construction and nothing else will ever fix.
    try:
        r = db.execute(sql_text("""
            SELECT count(*) FILTER (WHERE fandoms IS NULL OR cardinality(fandoms) = 0),
                   count(*)
            FROM stories TABLESAMPLE SYSTEM_ROWS(50000) WHERE site = 'ao3'
        """)).first()
        if r and r[1]:
            share = r[0] / r[1]
            out_ao3 = next((c for c in out if c["site"] == "ao3"), None)
            if out_ao3:
                out_ao3["unreachable_by_listing"] = int(share * out_ao3["total"])
    except Exception as e:
        log.info(f"unreachable estimate failed: {type(e).__name__}")
    out.sort(key=lambda c: -c["total"])
    return out


@router.get("/overview")
def overview(refresh: bool = False,
                   db: Session = Depends(get_db),
                   _admin: User = Depends(require_admin)):
    """Index health, crawl configuration and rate-limit state in one request."""
    global _CACHE, _CACHED_AT
    if _CACHE is not None and not refresh and (time.time() - _CACHED_AT) < _TTL:
        return {**_CACHE, "cached": True,
                "age_s": int(time.time() - _CACHED_AT)}
    out: dict = {}

    # Row counts from the planner's own estimate. Free, and accurate to a percent
    # or so on a table that is analysed regularly.
    rows = db.execute(sql_text("""
        SELECT relname, reltuples::bigint
        FROM pg_class WHERE relname IN ('stories','chapters','facets','takedowns')
    """)).fetchall()
    out["tables"] = {r[0]: max(0, int(r[1])) for r in rows}

    out["coverage"] = _coverage(db)

    # ...except for `stories`, where "analysed regularly" is exactly what a bulk
    # import breaks. reltuples only moves when autovacuum gets round to the
    # table, so during the import that makes the number interesting it lags by
    # millions — the page said 18M while /api/stats and the per-site totals
    # right below it said 20.1M, which reads as the index having lost 2M works.
    #
    # The per-site totals are exact counts and _coverage has just paid for them,
    # so summing them costs nothing and makes the two halves of the page agree.
    if out["coverage"]:
        exact_total = sum(int(c.get("total") or 0) for c in out["coverage"])
        if exact_total > 0:
            out["tables"]["stories"] = exact_total
    out["coverage_sample"] = _SAMPLE

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
def run_job(job: str, limit: int = Form(200),
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

    # _go is synchronous and blocking, and this endpoint runs in a FastAPI
    # threadpool thread, so asyncio.get_event_loop() here would raise
    # RuntimeError on py3.10+ (the job endpoint 500'd every call). Schedule it
    # through the loop-safe run_in_background helper instead, running _go on a
    # threadpool thread; the helper's done-callback also surfaces any error the
    # inner try/except missed.
    from live_fetch.jobs import run_in_background
    run_in_background(lambda: asyncio.to_thread(_go))
    return {"started": job, "limit": min(limit, 2000)}
