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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
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
    """Rows per site — from what /api/stats/sites has already computed.

    The docstring here used to say "from the planner rather than a scan". It was
    not: `SELECT site, count(*) FROM stories GROUP BY site` reads all 20.5M rows,
    it took nine seconds, and it ran with none of the protection the identical
    query in api/stats.py carries — no cache, no single-flight, no advisory
    lock, on a page an operator refreshes when they are already worried about
    something.

    Caught in the act: two of these aggregates running at once while autovacuum
    ANALYZEd the same table, and a cold `tags=Fluff` browse 503ing at 21.5s
    because it was starved of the disk behind them.

    So it asks for the figures rather than computing them. Falling back to the
    scan when nothing is cached is deliberate — a fresh install has to get the
    numbers from somewhere, and by the time anyone opens this panel on a running
    instance the widget in the header has long since filled that cache — but it
    takes the SAME advisory lock, so the two paths can no longer scan at once.
    """
    from api.stats import _SITES_LOCK_KEY, site_counts_without_scanning
    cached = site_counts_without_scanning()
    if cached:
        return cached
    # Nothing cached. Scan, but only if no one else is already scanning.
    if not db.execute(sql_text("SELECT pg_try_advisory_xact_lock(:k)"),
                      {"k": _SITES_LOCK_KEY}).scalar():
        log.info("site totals: another scan holds the lock; reporting none")
        return {}
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
     "Tells Bing, Yandex and Seznam that hub pages changed, rather than waiting "
     "to be crawled. Google does not participate. When this stops, the only "
     "thing telling those engines the site moved is their own crawl schedule.",
     36,
     # `indexnow_ran_at`, NOT `indexnow_watermark`. The watermark is a cursor
     # into hub content_at and always lags; reading it as a run time reported
     # 45.2h against a 36h budget while the loop had run 23 hours earlier and
     # submitted 5,534 URLs successfully. Falls back to the watermark so an
     # instance that has not run since this changed still shows something.
     "SELECT CAST(coalesce("
     "  (SELECT value FROM app_settings WHERE key = 'indexnow_ran_at'),"
     "  (SELECT value FROM app_settings WHERE key = 'indexnow_watermark')"
     ") AS timestamptz)"),
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
    # The crawler itself, which is thirteen of the worker's twenty loops and had
    # no row at all — enrichment, recent works, archive walks, listing harvest,
    # title repair, the two Wayback pairs. The panel exists because "a loop that
    # dies is silent", and it was silent about most of them.
    #
    # One row rather than thirteen, because they share one piece of evidence and
    # it is the honest one: every loop that fetches a work stamps `crawled_at`,
    # so a recent stamp means the fetching side is alive. It cannot say WHICH
    # loop is doing the work — for that, the queue depths below and the worker
    # log are the tools — but "nothing has been fetched for six hours" is the
    # alarm that matters, and nothing was raising it here.
    #
    # NOT `max(crawled_at)`. That is a sequential scan of 20.5M rows and it took
    # 15.7 SECONDS; the watchdog ran it every five minutes and it was the cause
    # of the site's intermittent slowness (see CLAUDE.md). The probe below stops
    # at the first matching row — 19 buffers while the crawler is healthy — and
    # a bounded window is all a staleness check ever needed.
    # The FF.net LIVE listing harvest, which is a separate container and had no
    # row here at all — so a third of the index quietly stopped receiving
    # updates and nothing on this page said so.
    #
    # Measured 2026-09-30: FF.net carries 380 works with an update date in the
    # last year against AO3's 883,281, and the harvester's own log held 41,717
    # consecutive 403s and no other status code. The site refuses this server
    # for content paths while still serving robots.txt, and the scheduler's
    # circuit breaker had already tripped `crawl_disabled_ffnet`. All of that
    # was true and none of it was visible as FRESHNESS.
    #
    # Evidence, not a heartbeat: written only when a listing page actually
    # answered. A loop turning happily against a site that refuses every
    # request is exactly what a heartbeat would call healthy.
    ("ffnet_listing", "FanFiction.net live harvest",
     "The browser harvester walking every FF.net fandom's listing pages, 25 "
     "works a page — now the main source of FF.net characters, pairings and "
     "genres, and of FF.net works the index lacked. Stale means FF.net has "
     "refused every session; the Wayback passes are the slow fallback.", 24,
     "SELECT to_timestamp(NULLIF(value,'')::bigint) FROM app_settings "
     " WHERE key = 'ffnet_listing_ok_at'"),
    # The other half of the same route. The harvester only STAGES; this row
    # moves when the worker actually WROTE staged rows into the index, so a
    # harvester answering happily while the apply loop has died reads stale.
    ("ffnet_listing_apply", "FF.net listing → index",
     "Writes what the harvester staged: fills characters, pairings, genres and "
     "ratings on works we hold, and adds the ones we did not. See "
     "ffnet_listing.py.", 6,
     "SELECT to_timestamp(NULLIF(value,'')::bigint) FROM app_settings "
     " WHERE key = 'ffnet_listing_applied_at'"),
    # What a fandom is recommending LATELY, which is the one recommendation
    # question no archive can answer — and which has produced nothing at all.
    #
    # Measured 2026-09-30: 8 threads discovered, **0 mentions, 0 works**. Every
    # pass ends "backed off for another 20965s"; the Reddit penalty has been
    # pinned at its 6h ceiling since 2026-09-29 17:54 and escalated
    # 15 -> 30 -> 60 -> 120 -> 240 -> 360 minutes without one success. The
    # module is unauthenticated by design (a User-Agent against public JSON,
    # no OAuth anywhere in this repo), and Reddit now refuses that from this
    # address — a condition the backoff cannot recover from, because every
    # probe is refused and re-penalises.
    #
    # The only trace of any of that was a WARNING in a 75,000-line worker log.
    # This is the row that says so. `read_at` is evidence and not a heartbeat:
    # it moves when a thread was actually READ, so a loop cheerfully backing
    # off forever reads as stale, which is what it is.
    ("rec_threads", "Recommendation threads",
     "Recent recommendation threads, which is how the index knows what a "
     "fandom is pressing on people NOW rather than all-time. Read from the "
     "public Reddit archives (Arctic Shift, PullPush) since Reddit itself "
     "stopped answering this server; stale here means the archives are down "
     "too — reddit_archive.py.", 72,
     "SELECT max(read_at) FROM rec_threads"),
    ("crawler", "Crawler fetching",
     "Thirteen loops fetch works: AO3 stubs and recent works, the archive walks, "
     "the fandom listing harvest, title repair and both Wayback passes. They all "
     "stamp crawled_at, so this is the one signal that says the fetching side is "
     "alive at all.", 6,
     "SELECT max(crawled_at) FROM (SELECT crawled_at FROM stories "
     " WHERE crawled_at > now() - interval '48 hours' LIMIT 200) s"),
    # Cheap, exact, and the reason several things on this page are trustworthy:
    # the planner's row estimates come from here, and CLAUDE.md records a
    # measurement taken while ANALYZE had never run that was wrong by 200x.
    ("analyze", "Table statistics",
     "ANALYZE keeps the query planner's estimates honest. When it has not run, "
     "the planner believes whatever it last saw — a measurement taken in that "
     "state once put `stories` at 84,799 rows instead of 20.5M and sent every "
     "conclusion drawn from it the wrong way.", 48,
     "SELECT greatest(last_analyze, last_autoanalyze) FROM pg_stat_user_tables "
     " WHERE relname = 'stories'"),
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
    # The AO3 Wayback queue was missing while its FF.net twin was listed, which
    # made the larger of the two invisible: 506,635 against 108,509.
    ("wayback", "AO3 Wayback queue", "SELECT count(*) FROM wayback_queue"),
    ("ffnet_wayback", "FF.net Wayback queue", "SELECT count(*) FROM ffnet_wayback_queue"),
    ("search_cache", "Shared search cache", "SELECT count(*) FROM search_cache_entries"),
]


# The archives by the names people call them, for a panel a person reads.
SITE_LABEL = {"ao3": "AO3", "ffnet": "FanFiction.net",
              "fictionalley": "FictionAlley"}

# What a missing field costs a reader, and how much it is worth fixing —
# imported, not declared here.
#
# This ranking and `gap_filler`'s queue used to carry separate weightings that
# disagreed about the most important thing: this said characters and pairings
# mattered most, that said summaries did. So the panel named a problem and the
# job that exists to fix it worked on something else. See field_priority.
from field_priority import COVERAGE_FIELD, FIELD_COST, FIELD_PRIORITY

# A gap has to be big enough to be worth a line. Below this it is a rounding
# error on a sample and reporting it is noise.
_GAP_MIN_SHARE = 0.25
_GAP_MIN_WORKS = 50_000


def _attention(evidence: list[dict], coverage: list[dict]) -> list[dict]:
    """What is actually wrong, worst first.

    The panel had all of this already and ranked none of it. Jobs were listed
    in the order they were declared, with `stale` as a flag somewhere down the
    list, and coverage as a table of percentages per site — so the
    cross-archive popularity pass sat broken for THIRTEEN DAYS with the
    evidence of it on screen the whole time, and "98% of 6.5M FanFiction.net
    works have no characters" read as one cell among twenty-one.

    Two kinds of problem, one ranking. A stale job is ranked by what it breaks;
    a coverage gap by HOW MANY WORKS IT COSTS, because that is the comparable
    number — a job three hours late matters less than six million works nobody
    can find by pairing, and only one of those two was ever visible.
    """
    out: list[dict] = []

    for j in evidence:
        if j.get("state") != "stale":
            continue
        age, limit = j.get("age_h") or 0, j.get("stale_after_h") or 1
        out.append({
            "kind": "job", "key": j["key"], "label": j["label"],
            "detail": (f"Last ran {age:,.0f}h ago, against a {limit}h limit. "
                       + (j.get("why") or "")).strip(),
            # How far past its own limit, so a job that is twice as late as it
            # should be outranks one that is barely over.
            # On the same scale as a gap, so the two orderings are one. A job
            # at twice its limit sits around the middle of the list, which is
            # where a stale weekly rebuild belongs against millions of works
            # nobody can find — and it climbs as it rots.
            "severity": 3.0 + 2.0 * (age / max(limit, 1)),
            "works": None,
        })

    for c in coverage:
        total, sampled = int(c.get("total") or 0), int(c.get("sampled") or 0)
        if not total or not sampled:
            continue
        na = set(c.get("na") or [])
        for field, name in COVERAGE_FIELD.items():
            if field in na or field not in c:
                continue
            share = int(c[field]) / sampled
            works = int(round(total * share))
            if share < _GAP_MIN_SHARE or works < _GAP_MIN_WORKS:
                continue
            out.append({
                "kind": "gap", "key": f"{c['site']}:{field}",
                "label": f"{SITE_LABEL.get(c['site'], c['site'])}: "
                         f"{FIELD_COST.get(name, name)}",
                "detail": (f"{works:,} works — {share * 100:.0f}% of "
                           f"{total:,} — "
                           + ("counted exactly." if c.get("exact")
                              else f"estimated from a {sampled:,}-row sample.")),
                # THREE THINGS, and the first version had only two of them.
                #
                # What the field is for, how many works it costs, and WHAT
                # SHARE OF THE ARCHIVE it covers — which was missing, and is
                # the difference between "a third of AO3 has no characters"
                # and "FanFiction.net effectively does not have the field".
                # At 98% the filter is not degraded for that archive, it is
                # absent.
                #
                # And uncapped. The first version capped at 5, which every one
                # of the top four gaps reached — so they tied and fell back to
                # the order the coverage rows happened to be in, putting a 32%
                # AO3 gap above a 98% FanFiction.net one. A cap that flattens
                # exactly the items it is meant to rank is worse than no cap;
                # the display band below is what bounds this for the eye.
                "severity": (FIELD_PRIORITY.get(name, 1)
                             * (works / 1_000_000) ** 0.4
                             * (0.4 + share)),
                "works": works,
            })

    out.sort(key=lambda a: -a["severity"])
    out = out[:12]
    # The BAND is decided here, not in the browser, so the thresholds live next
    # to the formula that produces the numbers they cut. Relative to the worst
    # thing on the list, because the absolute scale is unbounded by design.
    worst = max((a["severity"] for a in out), default=1.0) or 1.0
    for a in out:
        r = a["severity"] / worst
        a["band"] = "high" if r >= 0.66 else "mid" if r >= 0.33 else "low"
    return out


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

    # Ranked, and put where it cannot be missed. See `_attention`: everything
    # in it was already on this page and none of it was ordered by how much it
    # mattered.
    try:
        out["attention"] = _attention(
            (out.get("jobs") or {}).get("evidence") or [], out["coverage"])
    except Exception:
        log.info("attention ranking unavailable", exc_info=True)
        out["attention"] = []

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


# ── the passes an operator can start by hand ────────────────────────────────
#
# Every one of these is already on a timer. The buttons exist for "I have just
# fixed the thing that was blocking it and do not want to wait half an hour to
# find out whether it worked".
#
# The panel could see twenty-eight loops and start TWO of them, which is the
# wrong way round: the loops an operator most wants to poke are the heavy
# repairs, and those are exactly the ones with no button. The reason is visible
# in what this used to be — a two-entry dict of (module, description) plus an
# `if job == "ao3_stubs"` chain in the runner, because the two jobs happened to
# have different call signatures. Adding a third meant editing three places, so
# it never happened. The registry below makes the signature part of the entry,
# so adding a pass is one dict row.
#
# `dry_run` is the DEFAULT and not an option. Eight of these take one and every
# one of them implements it, so a dry run is the cheapest possible proof that a
# pass is wired up and the safest possible way to find out what it would do —
# `content_gates.run(dry_run=True)` reports the rows it would rewrite without
# touching a single one. An operator who wants the real thing has to say so, and
# the UI makes that a second click rather than the first one.
#
# `minutes` is a floor for "how long should I not leave this running", not a
# promise. popularity_rank measured 3h51m over 2.4M rows, so it says 240.
@dataclass(frozen=True)
class _Job:
    label: str
    blurb: str
    # Called as fn(limit, dry_run) -> str. Returns what the pass did or would do,
    # which is shown to the operator verbatim — a pass that reports nothing is a
    # pass whose result has to be found in a 75,000-line log.
    fn: Callable[[int, bool], str]
    # A pass that makes outbound requests is throttled by a rate limiter shared
    # with the crawler, so starting one by hand competes with indexing. Said in
    # the UI rather than prevented: an operator fixing a crawl blocker is
    # exactly the case where running it is right.
    networked: bool = False
    minutes: int = 0


def _wrap_simple(module: str, attr: str = "run") -> Callable[[int, bool], str]:
    """Adapter for the `run(dry_run=False)` shape eight of the passes share.

    The module is imported lazily inside the returned callable: importing all of
    them at module scope would pull the whole maintenance surface into every
    API worker, and one of them is the 3h51m popularity pass.
    """
    import importlib

    def call(limit: int, dry_run: bool) -> str:
        result = getattr(importlib.import_module(module), attr)(dry_run=dry_run)
        return _describe(result)

    return call


def _ao3_stubs(limit: int, dry_run: bool) -> str:
    import asyncio

    import ao3_stub_enrich

    asyncio.run(ao3_stub_enrich.enrich(limit, dry_run))
    return f"checked up to {limit:,} AO3 rows"


def _ffnet_meta(limit: int, dry_run: bool) -> str:
    from ffnet_enrich import run as enrich_run

    enrich_run(limit, dry_run, 0.0, 25, 600.0)
    return f"checked up to {limit:,} FF.net rows"


def _describe(result: object) -> str:
    """A pass's return value, in a sentence.

    Most of these return a dict of counters and one returns a bare int, and the
    dict is the useful part: `{"scanned": 847113, "fixed": 0}` says the pass ran
    and found nothing, which is the answer an operator wanted and the one a log
    line three screens up does not give them.
    """
    if result is None:
        return "finished"
    if isinstance(result, int):
        return f"{result:,}"
    if isinstance(result, dict):
        bits = [f"{k} {v:,}" if isinstance(v, int) else f"{k} {v}"
                for k, v in result.items() if v not in (None, "", [], {})]
        return ", ".join(bits) if bits else "nothing to do"
    return str(result)


_JOBS: dict[str, _Job] = {
    # ── repairs that rewrite `stories` in bulk ──
    "content_gates": _Job(
        "Recompute the content gates",
        "Re-derives the gate columns from the term lists after a list changes. "
        "The trigger has no way to retrofit a new term onto rows written before "
        "it, so this is the only thing that applies one.",
        _wrap_simple("content_gates"), minutes=45,
    ),
    "crossover": _Job(
        "Rebuild the crossover flag",
        "Recounts distinct franchises per work. The flag is derived from the "
        "row's own fandoms, so a trigger normally keeps it right — this is for "
        "a change to the definition itself.",
        _wrap_simple("crossover"), minutes=30,
    ),
    "series_wordcount": _Job(
        "Refresh series word totals",
        "The add-on that lets a long read be found when no single part is long "
        "enough. Sweeps until a sweep writes nothing.",
        _wrap_simple("series_wordcount"), minutes=30,
    ),
    "popularity_rank": _Job(
        "Rebuild the popularity percentile",
        "The only sort that compares archives honestly, and it runs offline over "
        "millions of rows. By far the longest pass here — budget hours, not "
        "minutes.",
        _wrap_simple("popularity_rank"), minutes=240,
    ),
    # ── passes that fetch from a third party ──
    "ao3_stubs": _Job(
        "Fill AO3 rows with no fandom",
        "Only the rows the listing harvest cannot reach, worst gaps first. One "
        "request each, so this is deliberately the small half of the job.",
        _ao3_stubs, networked=True,
    ),
    "ffnet_meta": _Job(
        "Backfill FF.net characters",
        "From Wayback captures — FF.net publishes characters and pairings, but "
        "neither bulk dump carries them.",
        _ffnet_meta, networked=True,
    ),
    "ao3_title_repair": _Job(
        "Repair truncated AO3 titles",
        "The bulk metadata dump ships titles cut mid-phrase, and 688k rows are "
        "affected. One request per work, so it will not catch up.",
        lambda limit, dry: _ao3_titles(limit, dry), networked=True,
    ),
    "indexnow": _Job(
        "Submit changed hub URLs",
        "Tells search engines which hub pages moved. Bounded by the key; does "
        "nothing without one.",
        lambda limit, dry: str(_indexnow(limit, dry)), networked=True,
    ),
    "reddit_recs": _Job(
        "Import reader recommendations",
        "Community rec lists, written as the `community_recs` marker the recs "
        "filter reads. Reads r/HPFanfiction's most-linked list from its public "
        "spreadsheet, so it does not depend on Reddit answering.",
        lambda limit, dry: _describe(_reddit_recs(dry)), networked=True,
    ),
    "tropedia_recs": _Job(
        "Import Tropedia recommendations",
        "The same marker from ~900 fandom wikis. Commits in batches of 25 "
        "pages, so an interruption costs the pages not yet read.",
        lambda limit, dry: _describe(_tropedia_recs(limit, dry)), networked=True,
    ),
}


def _ao3_titles(limit: int, dry_run: bool) -> str:
    import ao3_title_repair

    ao3_title_repair.run(limit, dry_run, 0.0)
    return f"checked up to {limit:,} truncated titles"


def _indexnow(limit: int, dry_run: bool) -> int:
    import indexnow

    return indexnow.run(limit, dry_run)


def _reddit_recs(dry_run: bool):
    import reddit_recs_import

    return reddit_recs_import.run(dry_run)


def _tropedia_recs(limit: int, dry_run: bool):
    import tropedia_recs_import

    return tropedia_recs_import.run(dry_run, limit or None)


# Where a pass RUNS, and where its state lives, is admin_jobs.py — read its
# docstring before moving either back here. Short version: in-process state was
# wrong on 3 polls in 4 with WEB_CONCURRENCY=4, and the API is the container a
# deploy restarts under a four-hour pass.


def _job_view(db, key: str, rec: dict | None, seen: float | None) -> dict:
    import admin_jobs
    out = dict(rec) if rec else {"job": key, "state": "never-run"}
    # Queued with nobody listening is a distinct state, and the panel must be
    # able to say it: the public tier has no worker of its own, and a dev worker
    # that is down or running older code would otherwise leave "queued" on
    # screen for ever, indistinguishable from "about to start".
    if out.get("state") == "queued" and (seen is None or seen > admin_jobs.WORKER_SILENT_S):
        out["stalled"] = True
    return out


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """What can be started, each pass's last outcome, and whether the worker is
    listening — one call, so the panel renders state on load instead of only
    for passes started in this tab.

    The registry is still the single list; see `_JOBS`.
    """
    import admin_jobs
    state = admin_jobs.read_all(db)
    seen = admin_jobs.worker_seen_s(db)
    return {
        "worker_seen_s": None if seen is None else round(seen),
        "worker_silent_after_s": admin_jobs.WORKER_SILENT_S,
        "history": admin_jobs.history(db)[:12],
        "jobs": [
            {
                "key": key,
                "label": j.label,
                "blurb": j.blurb,
                "networked": j.networked,
                "minutes": j.minutes,
                "last": _job_view(db, key, state.get(key), seen),
            }
            for key, j in _JOBS.items()
        ],
    }


@router.post("/run/{job}")
def run_job(job: str, limit: int = Form(200), dry_run: bool = Form(True),
            db: Session = Depends(get_db),
            _admin: User = Depends(require_admin)):
    """Queue one pass for the worker — a DRY RUN unless asked otherwise.

    Single-flight across every process: a pass already queued or running is a
    409, decided in SQL rather than in this process's memory.
    """
    if job not in _JOBS:
        raise HTTPException(404, f"No such job. Known: {', '.join(sorted(_JOBS))}")
    import admin_jobs
    cap = min(max(int(limit), 1), 2000)
    try:
        rec = admin_jobs.request(db, job, cap, bool(dry_run))
    except admin_jobs.Busy:
        raise HTTPException(409, "That pass is already queued or running.")
    return _job_view(db, job, rec, admin_jobs.worker_seen_s(db))


@router.post("/run/{job}/cancel")
def cancel_job(job: str, db: Session = Depends(get_db),
               _admin: User = Depends(require_admin)):
    """Withdraw a queued pass. A running one cannot be stopped from here."""
    if job not in _JOBS:
        raise HTTPException(404, f"No such job. Known: {', '.join(sorted(_JOBS))}")
    import admin_jobs
    if not admin_jobs.cancel(db, job):
        raise HTTPException(409, "Only a queued pass can be withdrawn.")
    return _job_view(db, job, admin_jobs.read(db, job), admin_jobs.worker_seen_s(db))


@router.get("/run/{job}")
def job_result(job: str, db: Session = Depends(get_db),
               _admin: User = Depends(require_admin)):
    """How the last run of a pass went, including its own report. Read from the
    database, so every API worker gives the same answer."""
    if job not in _JOBS:
        raise HTTPException(404, f"No such job. Known: {', '.join(sorted(_JOBS))}")
    import admin_jobs
    return _job_view(db, job, admin_jobs.read(db, job), admin_jobs.worker_seen_s(db))


def run_claimed(rec: dict) -> dict:
    """Execute one claimed request and record the outcome. Called by the worker,
    in a thread; never by a request handler."""
    import admin_jobs
    from db.session import db_session
    from maintenance_lock import MaintenanceDeferred
    spec = _JOBS.get(rec["job"])
    try:
        if spec is None:
            raise RuntimeError("no longer in the registry")
        report = spec.fn(int(rec.get("limit") or 200), bool(rec.get("dry_run", True)))
        with db_session() as db:
            return admin_jobs.finish(db, rec, report=report)
    except MaintenanceDeferred as e:
        # Not a failure: another heavy pass holds the shared slot. Said as such,
        # so the operator waits rather than debugging.
        with db_session() as db:
            return admin_jobs.finish(db, rec, deferred=True,
                                     error=f"another heavy pass is running ({e})")
    except Exception as e:
        log.warning(f"admin job {rec.get('job')} failed: {type(e).__name__}: {e}")
        with db_session() as db:
            return admin_jobs.finish(db, rec, error=f"{type(e).__name__}: {e}")


@router.get("/users")
def list_users(db: Session = Depends(get_db),
               _admin: User = Depends(require_admin)):
    """Who has an account here, and whether they could get back into it.

    The operator could always read this with psql, and did — which is the
    argument for putting it on the page rather than against it. The question it
    answers is not "who are my users" on a site with two of them; it is "can
    this person recover their password", and the answer lives in a column
    (`email`) that nothing in the UI ever showed for anybody but yourself.

    The address is shown, not merely its presence. Without SMTP configured the
    reset flow works by an operator reading a code out of the pending list and
    passing it on (see api/password_reset.py), and you cannot pass it on to an
    address you cannot see. Withholding it from the one person who already has
    database access would be theatre.

    Counted per account rather than joined in one query: there are two accounts
    on this instance and the largest of these tables has 29 rows, so the clarity
    is free. If this ever lists thousands, make it one query with LEFT JOINs
    before it makes anything else slower.
    """
    users = db.query(User).order_by(User.created_at.asc()).all()
    out = []
    for u in users:
        counts = db.execute(sql_text("""
            SELECT
              (SELECT count(*) FROM user_sessions WHERE user_id = :id
                                AND expires_at > now())                AS sessions,
              (SELECT count(*) FROM follows      WHERE user_id = :id)  AS follows,
              (SELECT count(*) FROM user_data    WHERE user_id = :id)  AS saved,
              (SELECT count(*) FROM user_hosted  WHERE user_id = :id)  AS imported,
              -- When they were last HERE, which is not when they last logged in.
              --
              -- `users.last_login` is written when somebody types a password,
              -- and a remembered session then rolls its cookie forward for
              -- ninety days without ever touching it again. So the column reads
              -- as "last seen" and is not: measured on this instance, the owner
              -- showed 2026-08-15 while using the site that minute, because the
              -- last time they typed a password was twenty-four days earlier.
              --
              -- `user_sessions.last_used` is the real signal — every
              -- authenticated request stamps it (at most every 15 minutes; see
              -- the reissue note in api/auth.py). Expired sessions count too:
              -- a session that has since lapsed is still evidence of when its
              -- owner was last here.
              (SELECT max(last_used) FROM user_sessions WHERE user_id = :id)
                                                                       AS seen
        """), {"id": str(u.id)}).first()
        # The later of the two, so an account that logged in and never came back
        # still reports the login, and one that never logs out reports today.
        seen = counts[4]
        if u.last_login and (seen is None or u.last_login > seen):
            seen = u.last_login
        out.append({
            "username": u.username,
            "role": getattr(u.role, "value", str(u.role)),
            "email": u.email,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            # Kept, and it answers a different question: when did this person
            # last prove they know the password. Useful when somebody says they
            # are locked out.
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "last_seen": seen.isoformat() if seen else None,
            # The one that matters, said as a fact rather than left to be
            # inferred from a blank cell.
            "recoverable": bool(u.email),
            "sessions": int(counts[0] or 0),
            "follows": int(counts[1] or 0),
            "saved": int(counts[2] or 0),
            "imported": int(counts[3] or 0),
        })
    return {"users": out}


# ── what the database is doing right now ────────────────────────────────────
#
# Three of the slow-site investigations recorded in CLAUDE.md ended the same way:
# the search was not slow, something ELSE was scanning `stories` — the watchdog's
# max(crawled_at), a second copy of the per-site count in this very module, two
# heavy passes colliding every week. Each was found by somebody running
# pg_stat_activity by hand. This is that query, on the page where you go when
# the site feels slow.
@router.get("/activity")
def db_activity(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Live connections, what each is running, and who is waiting on whom.

    Owner-only, and the query text is shown: it can contain a reader's search
    terms, which the owner already sees on the Audience tab.
    """
    rows = db.execute(sql_text("""
        SELECT a.pid, a.application_name, a.state, a.wait_event_type, a.wait_event,
               a.backend_type,
               EXTRACT(EPOCH FROM (now() - a.query_start))  AS running_s,
               EXTRACT(EPOCH FROM (now() - a.xact_start))   AS xact_s,
               left(regexp_replace(a.query, '\\s+', ' ', 'g'), 400) AS query,
               cardinality(pg_blocking_pids(a.pid))          AS blocked_by,
               a.backend_xmin IS NOT NULL                    AS holds_snapshot
          FROM pg_stat_activity a
         WHERE a.datname = current_database()
           AND a.pid <> pg_backend_pid()
    """)).mappings().all()
    conns = db.execute(sql_text("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE state = 'active')               AS active,
               count(*) FILTER (WHERE state = 'idle in transaction')  AS idle_in_xact,
               current_setting('max_connections')::int                AS max
          FROM pg_stat_activity
    """)).mappings().first()
    # Running queries, plus idle-in-transaction sessions ONLY when they hold a
    # snapshot (backend_xmin) — the kind that stops VACUUM cleaning the whole
    # database. An idle transaction holding nothing (a lock-only connection)
    # is not work and was being reported as an 8,547s "longest query".
    busy = [dict(r) for r in rows
            if r["backend_type"] == "client backend"
            and (r["state"] == "active"
                 or (r["state"] or "").startswith("idle in transaction") and r["holds_snapshot"])]
    busy.sort(key=lambda r: -(r["running_s"] or 0))
    for r in busy:
        r["running_s"] = round(float(r["running_s"] or 0), 1)
        r["xact_s"] = round(float(r["xact_s"] or 0), 1)
    return {
        "connections": dict(conns),
        "running": busy[:40],
        "waiting_on_locks": sum(1 for r in busy if r["blocked_by"]),
        "longest_s": max((r["running_s"] for r in busy if r["state"] == "active"), default=0),
    }


# ── one work, everything the index holds about it ───────────────────────────
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _story_key(q: str) -> tuple[str, str] | None:
    q = (q or "").strip()
    m = re.search(r"fanfiction\.net/s/(\d+)", q)
    if m:
        return ("ffnet", m.group(1))
    m = re.search(r"archiveofourown\.org/works/(\d+)", q)
    if m:
        return ("ao3", m.group(1))
    m = re.fullmatch(r"(ao3|ffnet|fictionalley)[:/ ](\d+)", q, re.I)
    if m:
        return (m.group(1).lower(), m.group(2))
    return None


@router.get("/story")
def inspect_story(q: str, db: Session = Depends(get_db),
                  _admin: User = Depends(require_admin)):
    """Look one work up by id, archive URL or `ao3:123`, and say what the index
    believes about it — including whether its cached gate columns still agree
    with the term lists.

    That last check is the reason this exists. The gate columns are a CACHE of
    gate_terms.py (see CLAUDE.md, "Belt and braces is permanent"), and when a
    reader reports a work turning up where it should not, the first question is
    whether the column is stale or the term list is missing something. Before
    this, answering it meant psql plus reading the trigger.
    """
    import gate_terms
    from crossover import is_crossover

    m = _UUID.search(q or "")
    if m:
        where, params = "id = CAST(:id AS uuid)", {"id": m.group(0)}
    else:
        key = _story_key(q)
        if not key:
            raise HTTPException(400, "Give a story id, an AO3 or FanFiction.net URL, or ao3:<id>.")
        where, params = "site = :site AND site_id = :sid", {"site": key[0], "sid": key[1]}
    row = db.execute(sql_text(f"""
        SELECT id, site::text AS site, site_id, url, title, author, rating::text AS rating,
               status::text AS status, language, word_count, chapter_count,
               chapter_count_total, kudos, bookmarks, hits, comments, popularity,
               gate_underage, gate_adult, is_crossover, series_total_words,
               fandoms, relationships, characters, tags, warnings,
               is_hosted, delisted_at, delisted_reason, text_withdrawn_at,
               published_at, updated_at, crawled_at, indexed_at,
               summary IS NOT NULL AND summary <> '' AS has_summary
          FROM stories WHERE {where} LIMIT 1
    """), params).mappings().first()
    if not row:
        raise HTTPException(404, "Not in the index.")
    r = dict(row)
    warnings, tags = set(r["warnings"] or []), set(r["tags"] or [])
    want_u = bool(warnings & set(gate_terms.UNDERAGE_WARNINGS) or tags & set(gate_terms.UNDERAGE_TAGS))
    want_a = bool(warnings & set(gate_terms.ADULT_WARNINGS) or tags & set(gate_terms.ADULT_TAGS))
    r["gates"] = {
        "underage": {"column": r.pop("gate_underage"), "lists": want_u,
                     "terms": sorted((warnings | tags) & (set(gate_terms.UNDERAGE_WARNINGS) | set(gate_terms.UNDERAGE_TAGS)))},
        "adult": {"column": r.pop("gate_adult"), "lists": want_a,
                  "terms": sorted((warnings | tags) & (set(gate_terms.ADULT_WARNINGS) | set(gate_terms.ADULT_TAGS)))},
    }
    r["crossover_now"] = is_crossover(r["fandoms"] or [])
    r["chapters_stored"] = db.execute(sql_text(
        "SELECT count(*) FROM chapters WHERE story_id = :i"), {"i": r["id"]}).scalar()
    r["series"] = [dict(s) for s in db.execute(sql_text("""
        SELECT se.id, se.name AS title, sw.position, se.member_count, se.total_words
          FROM series_works sw JOIN series se ON se.id = sw.series_id
         WHERE sw.story_id = :i ORDER BY sw.position NULLS LAST LIMIT 10
    """), {"i": r["id"]}).mappings()]
    for k, v in list(r.items()):
        if isinstance(v, datetime):
            r[k] = v.isoformat()
    r["id"] = str(r["id"])
    for s in r["series"]:
        s["id"] = str(s["id"])
    return r
