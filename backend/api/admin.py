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

import json
import logging
import os
import time
from datetime import datetime, timezone

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


# How long each background job may go quiet before the page says so. These are
# the job's own interval with room to spare, not a guess: a hub rebuild every
# 24h flagged at 24h would show amber on a healthy system every single day.
_JOB_EVIDENCE = [
    # key, label, what it proves, hours before "stale", the SQL for its evidence
    ("hubs", "Hub rebuild",
     "Rebuilds the 11,190 fandom and ship pages crawlers walk. If this stops, "
     "the only route search engines have into the index goes stale.", 36,
     "SELECT max(built_at) FROM fandom_hubs"),
    ("hub_content", "Hub content change",
     "The last time a hub's listing actually changed, as opposed to being "
     "rebuilt identically. Feeds the sitemap's lastmod.", 72,
     "SELECT max(content_at) FROM ship_hubs"),
    ("indexnow", "IndexNow submission",
     "Tells Bing, Yandex and Seznam which hub pages changed. The only push "
     "channel there is — everything else waits to be crawled.", 36,
     "SELECT CAST(value AS timestamptz) FROM app_settings "
     "WHERE key = 'indexnow_watermark'"),
    ("ship_aliases", "Ship nickname mining",
     "Maps 'dramione' and 'drarry' onto their canonical pairings. A search "
     "for a nickname finds almost nothing without it.", 200,
     "SELECT max(built_at) FROM ship_aliases"),
    # The job this whole panel was written for, and the one row it did not have.
    # Its absence cost exactly what the panel exists to prevent: the weekly loop
    # left no trace but a line in a 75,000-line worker log, and the score sat at
    # 549,515 works while 2,399,048 carried an engagement figure — "Most
    # popular" silently covering 2.7% of the index instead of 11.7%.
    #
    # 200h rather than the weekly 168h, so an ordinary late run is not an alarm.
    ("popularity", "Cross-archive popularity",
     "Scores every work that has an engagement figure onto one scale, which is "
     "what 'Most popular' sorts by. Frozen, it silently shrinks that sort to "
     "whatever the last run covered.", 200,
     "SELECT CAST(value AS timestamptz) FROM app_settings "
     "WHERE key = 'popularity_built_at'"),
    # NOT here, deliberately: the AO3 stale-WIP refresh. The obvious evidence for
    # it is max(queued_at) on ao3_refresh_queue, and that is the wrong column —
    # the queue is REFILLED in batches and drained a few at a time, so all 160
    # rows carry one timestamp from the last refill. It read 34.3h old and was
    # flagged stale on the first render of this panel while the loop was in fact
    # running every 40 minutes and had logged a pass four minutes earlier. A
    # panel that cries wolf is worse than no panel. Its queue DEPTH is honest and
    # is in _JOB_QUEUES below; its liveness has no cheap evidence, so nothing
    # here claims to measure it.
    ("traffic", "Traffic recording",
     "The buffered writer behind pageview and search stats. It drops events "
     "rather than blocking a page, so it can fail without anything breaking.",
     24, "SELECT max(at) FROM visit_events"),
    ("series_fill", "Series detection",
     "Groups works into series. Stalled indefinitely once before, on a SQL "
     "error nothing surfaced.", 48,
     "SELECT max(attempted_at) FROM series_fill_log"),
]

# Queues, which say the opposite thing to a timestamp: not "did it run" but
# "is it keeping up". A queue that only grows is a job that is running and
# losing.
_JOB_QUEUES = [
    # Not a queue in the usual sense — the works that HAVE an engagement figure
    # and are still waiting for a score. It belongs here rather than beside the
    # timestamp above because it answers the other question: a popularity pass
    # that runs on time and falls further behind every week looks healthy from a
    # timestamp alone. Read from what the last pass recorded, because counting
    # it live is a sequential scan of 20M rows.
    ("popularity_backlog", "Works awaiting a popularity score",
     "SELECT GREATEST(0, "
     "  COALESCE((SELECT value FROM app_settings WHERE key='popularity_eligible'), '0')::bigint"
     "  - COALESCE((SELECT value FROM app_settings WHERE key='popularity_scored'), '0')::bigint)"),
    ("ao3_refresh", "AO3 stale-WIP queue", "SELECT count(*) FROM ao3_refresh_queue"),
    ("ffnet_wayback", "FF.net Wayback queue", "SELECT count(*) FROM ffnet_wayback_queue"),
    ("search_cache", "Shared search cache", "SELECT count(*) FROM search_cache_entries"),
]


def _job_evidence(db: Session) -> dict:
    """What each background job last managed to DO, and how stale that is."""
    now = datetime.now(timezone.utc)
    jobs = []
    for key, label, why, stale_h, sql in _JOB_EVIDENCE:
        last, age_h = None, None
        try:
            val = db.execute(sql_text(sql)).scalar()
            if val is not None:
                if val.tzinfo is None:
                    val = val.replace(tzinfo=timezone.utc)
                last = val.isoformat()
                age_h = round((now - val).total_seconds() / 3600, 1)
        except Exception:
            db.rollback()          # a missing table must not take the page down
        jobs.append({"key": key, "label": label, "why": why,
                     "last_run": last, "age_h": age_h, "stale_after_h": stale_h,
                     "state": ("unknown" if age_h is None
                               else "stale" if age_h > stale_h else "ok")})

    queues = []
    for key, label, sql in _JOB_QUEUES:
        try:
            queues.append({"key": key, "label": label,
                           "depth": int(db.execute(sql_text(sql)).scalar() or 0)})
        except Exception:
            db.rollback()
    return {"evidence": jobs, "queues": queues}


_GROWTH_KEY = "admin_growth_samples"
_GROWTH_MAX = 60


def _growth_sample(db: Session, total: int) -> dict:
    """Append one (time, total) sample and read the rate off the ends.

    Deliberately not a query over `stories.indexed_at`: that has no index and
    measured 15.7s. Samples cost nothing and get more accurate on their own.
    """
    now = datetime.now(timezone.utc)
    try:
        raw = get_setting(db, _GROWTH_KEY)
        samples = json.loads(raw) if raw else []
    except Exception:
        samples = []
    if not isinstance(samples, list):
        samples = []

    if total > 0:
        samples.append([now.isoformat(), int(total)])
        samples = samples[-_GROWTH_MAX:]
        try:
            put_setting(db, _GROWTH_KEY, json.dumps(samples))
            db.commit()
        except Exception:
            db.rollback()

    # Need two samples at least an hour apart before quoting a daily rate; a
    # ten-minute window multiplied up to a day is noise wearing a number's
    # clothes.
    if len(samples) >= 2:
        try:
            t0 = datetime.fromisoformat(samples[0][0])
            t1 = datetime.fromisoformat(samples[-1][0])
            hours = (t1 - t0).total_seconds() / 3600
            if hours >= 1:
                delta = samples[-1][1] - samples[0][1]
                return {"per_day": int(round(delta / hours * 24)),
                        "window_h": round(hours, 1),
                        "samples": len(samples)}
        except Exception:
            pass
    return {"per_day": None, "window_h": None, "samples": len(samples)}


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

    # ── Is anything actually running? ────────────────────────────────────────
    #
    # The page could say a great deal about what the index CONTAINS and nothing
    # about whether the machinery filling it is alive. That is the gap that
    # matters: `popularity_rank.py` once had no loop at all and sat frozen at
    # whatever the last manual run produced, and nothing anywhere would have
    # shown it. A loop that dies is silent by construction.
    #
    # Every figure here is EVIDENCE THE LOOP ITSELF LEFT — a build timestamp, a
    # watermark, a queue draining — rather than a heartbeat the loop reports.
    # That is deliberate: a heartbeat says "I ran", this says "I did something",
    # and the second is the one that catches a loop running happily over a
    # broken query. It also means no worker changes, so nothing here can affect
    # indexing.
    out["jobs"] = _job_evidence(db)

    # ── Where the disk went ──────────────────────────────────────────────────
    # 30ms, and the reason it is worth having on the page: this database is 39GB
    # on a home box, and the two facts that matter (which object is biggest, and
    # whether it is growing) previously needed psql.
    try:
        out["storage"] = {
            "db_bytes": int(db.execute(sql_text(
                "SELECT pg_database_size(current_database())")).scalar() or 0),
            "objects": [
                {"name": r[0], "bytes": int(r[1]), "rows": max(0, int(r[2]))}
                for r in db.execute(sql_text("""
                    SELECT relname, pg_total_relation_size(relid), n_live_tup
                      FROM pg_stat_user_tables
                     ORDER BY pg_total_relation_size(relid) DESC LIMIT 8
                """)).fetchall()
            ],
        }
    except Exception:
        out["storage"] = None

    # ── Growth, without paying for it ────────────────────────────────────────
    #
    # "Works added per day" is the one number that says the crawler is alive,
    # and the obvious query for it — grouping `stories` by indexed_at — is a
    # sequential scan of 20M rows that measured 15.7 SECONDS. There is no index
    # on indexed_at and adding one costs ~600MB on a disk already at 71%, which
    # is a bad trade for a chart.
    #
    # So it samples itself instead: each uncached overview appends (now, total)
    # to a short list and the rate is read off the ends. The exact total is
    # already computed above by _coverage, so this costs one small write. It
    # says "collecting" for the first few hours and is accurate after that,
    # which is the right shape for a number nobody needs to the minute.
    out["growth"] = _growth_sample(db, out["tables"].get("stories") or 0)

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
