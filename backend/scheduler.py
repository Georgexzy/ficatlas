"""Background scheduler — runs crawls automatically on a schedule.

Intervals are controlled by environment variables so you can tune them
without code changes:

  CRAWL_INTERVAL_AO3_HOURS    default: 6
  CRAWL_INTERVAL_FFNET_HOURS  default: 4
  CRAWL_RUN_ON_STARTUP        default: true  (run once immediately at boot)
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

AO3_INTERVAL_HOURS   = float(os.getenv("CRAWL_INTERVAL_AO3_HOURS",  "6"))
FFNET_INTERVAL_HOURS = float(os.getenv("CRAWL_INTERVAL_FFNET_HOURS", "4"))
RUN_ON_STARTUP       = os.getenv("CRAWL_RUN_ON_STARTUP", "true").lower() == "true"

# Circuit breaker: if a site's scheduled crawl racks up too many failures inside
# the window, auto-disable that site so it stops hammering a blocked endpoint and
# filling the log. The user re-enables it from Settings once connectivity is fixed.
CRAWL_FAIL_THRESHOLD = int(os.getenv("CRAWL_FAIL_THRESHOLD", "5"))      # failures…
CRAWL_FAIL_WINDOW_H  = float(os.getenv("CRAWL_FAIL_WINDOW_HOURS", "6")) # …within this many hours

scheduler = AsyncIOScheduler(timezone="UTC")
_last_run: dict[str, datetime | None] = {"ao3": None, "ffnet": None}
_next_run: dict[str, datetime | None] = {"ao3": None, "ffnet": None}
_startup_task = None  # holds reference to startup poll so it isn't GC'd


async def _direct_crawl_enabled() -> bool:
    """Whether direct site crawling is on. The settings toggle (DB) takes priority;
    the ENABLE_DIRECT_CRAWL env var is the fallback default. Checked at each run so
    the toggle works without a restart."""
    try:
        from db.session import db_session
        from api.settings import get_setting
        with db_session() as db:
            val = get_setting(db, "enable_direct_crawl")
            if val:  # explicit DB value wins
                return str(val).lower() == "true"
    except Exception:
        pass
    return os.getenv("ENABLE_DIRECT_CRAWL", "false").lower() == "true"


def _classify_failure(err: str) -> str:
    """Decide whether a crawl error means the site is genuinely BLOCKED (counts
    toward the circuit breaker) or just TRANSIENT/slow (does not).

    From observed behaviour:
      - blocked   → 403 (Cloudflare wall, e.g. FF.net), 401, "all fallbacks
                    failed", connection refused / name resolution errors.
      - transient → ReadTimeout / ConnectTimeout (AO3 just slow), 502/503/504,
                    and a 525 (Cloudflare-couldn't-reach-origin) which is usually
                    a momentary blip that succeeds on retry.
    Anything unrecognised is treated as transient, so we err on NOT disabling a
    site that might still be reachable.
    """
    low = err.lower()
    blocked_markers = ("403", "401 ", "forbidden", "all fallbacks failed",
                       "connection refused", "name or service not known",
                       "nodename nor servname", "ssl", "certificate")
    transient_markers = ("readtimeout", "connecttimeout", "timeout", "timed out",
                         "502", "503", "504", "525", "connectionreset",
                         "remoteprotocolerror", "temporarily")
    # Transient wins over blocked when both appear. A 525 is Cloudflare failing to
    # reach AO3's origin and clears on its own, but the message for one often also
    # carries a blocked marker — most of all when a fallback host answered 403,
    # producing "all fallbacks failed ... (last status: 403)". Checking blocked
    # first therefore recorded ordinary AO3 slowness as a block, and five of those
    # in six hours auto-disabled AO3 crawling.
    if any(m in low for m in transient_markers):
        return "transient"
    if any(m in low for m in blocked_markers):
        return "blocked"
    return "transient"


def _site_crawl_disabled(db, site: str) -> bool:
    """Has the circuit breaker auto-disabled this site?"""
    try:
        from api.settings import get_setting
        return str(get_setting(db, f"crawl_disabled_{site}")).lower() == "true"
    except Exception:
        return False


def _consecutive_blocked(db, site: str, look: int = 12) -> int:
    """How many of this site's most recent crawls failed as blocked, in a row.

    The window-based count below could not catch the case it most needed to.
    A permanently blocked site does not fail in a burst — it fails once per
    crawl interval, forever. With a threshold of 5 failures in 6 hours and a
    crawl every few hours, at most one or two ever landed in a window, so the
    breaker never tripped: FanFiction.net accumulated 63 consecutive 403s over
    a fortnight, retried on schedule the whole time, and stayed "enabled".

    Consecutive failures are the right signal for a permanent block because they
    are independent of how often we try. A single success resets it, so a site
    that is merely flaky is never disabled.
    """
    from models.story import CrawlJob, SiteEnum
    try:
        recent = (db.query(CrawlJob)
                  .filter(CrawlJob.site == SiteEnum(site),
                          CrawlJob.job_type == "incremental")
                  .order_by(CrawlJob.created_at.desc())
                  .limit(look).all())
    except Exception:
        return 0
    run = 0
    for job in recent:
        if job.status == "failed" and (job.error or "").startswith("[blocked]"):
            run += 1
        elif job.status == "done":
            break        # a success ends the streak
        # A transient failure neither extends nor breaks the streak: AO3 being
        # slow in the middle of a run of 403s says nothing either way.
    return run


def _recent_blocked_count(db, site: str) -> int:
    """Count this site's BLOCKED crawl failures within the breaker window.
    Slow/transient failures (tagged "[transient]") are deliberately excluded so
    AO3 being slow never trips the breaker."""
    from datetime import timedelta
    from models.story import CrawlJob, SiteEnum
    cutoff = datetime.now(timezone.utc) - timedelta(hours=CRAWL_FAIL_WINDOW_H)
    try:
        return (db.query(CrawlJob)
                .filter(CrawlJob.site == SiteEnum(site),
                        CrawlJob.status == "failed",
                        CrawlJob.error.like("[blocked]%"),
                        CrawlJob.started_at >= cutoff)
                .count())
    except Exception:
        return 0


def _trip_breaker(db, site: str, fail_count: int):
    """Auto-disable a site after too many failures, logged once."""
    from api.settings import put_setting
    put_setting(db, f"crawl_disabled_{site}", "true")
    logger.warning(
        f"[scheduler] CIRCUIT BREAKER: {site} crawl auto-disabled after "
        f"{fail_count} failures in {CRAWL_FAIL_WINDOW_H:g}h. "
        f"Re-enable in Settings once connectivity is restored."
    )


async def _run_crawl(site: str):
    """Wrapper that records timing and logs progress."""
    # Honour the runtime toggle — skip quietly when direct crawling is disabled.
    if not await _direct_crawl_enabled():
        logger.debug(f"[scheduler] direct crawl disabled, skipping {site}")
        return

    from crawlers.ao3 import AO3Crawler
    from crawlers.ffnet import FFNetCrawler
    from models.story import CrawlJob, SiteEnum
    from db.session import db_session

    CRAWLERS = {"ao3": AO3Crawler, "ffnet": FFNetCrawler}
    if site not in CRAWLERS:
        return

    # Circuit breaker: skip sites that have been auto-disabled after repeated
    # failures. (User clears crawl_disabled_<site> in Settings to re-enable.)
    with db_session() as db:
        if _site_crawl_disabled(db, site):
            logger.debug(f"[scheduler] {site} crawl is circuit-broken (auto-disabled), skipping")
            return

    logger.info(f"[scheduler] Starting incremental crawl: {site}")
    _last_run[site] = datetime.now(timezone.utc)

    job_record = None
    with db_session() as db:
        job_record = CrawlJob(
            site=SiteEnum(site),
            job_type="incremental",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(job_record)
        db.flush()
        job_id = str(job_record.id)

    crawler = None
    try:
        crawler = CRAWLERS[site]()
        stats = await crawler.run(job_type="incremental")
        logger.info(f"[scheduler] {site} crawl done: {stats}")

        with db_session() as db:
            from models.story import CrawlJob as CJ
            import uuid
            j = db.query(CJ).filter(CJ.id == uuid.UUID(job_id)).first()
            if j:
                j.status = "done"
                j.stories_found   = stats.get("found", 0)
                j.stories_new     = stats.get("new", 0)
                j.stories_updated = stats.get("updated", 0)
                j.finished_at     = datetime.now(timezone.utc)

    except Exception as e:
        err = str(e)
        kind = _classify_failure(err)
        logger.error(f"[scheduler] {site} crawl failed ({kind}): {err}")
        with db_session() as db:
            from models.story import CrawlJob as CJ
            import uuid
            j = db.query(CJ).filter(CJ.id == uuid.UUID(job_id)).first()
            if j:
                # Tag the failure kind in the status so the breaker can count only
                # genuine blocks, not AO3 being slow. "failed" stays the umbrella
                # so existing status checks keep working; the detail is in error.
                j.status = "failed"
                j.error  = f"[{kind}] {err}"
                j.finished_at = datetime.now(timezone.utc)
            db.commit()
            # Only BLOCKED failures count toward the breaker. Transient ones
            # (ReadTimeout = AO3 just slow, 502/503/504, single retryable 525)
            # must not disable a site that's actually reachable.
            if kind == "blocked":
                # Two ways in. The window catches a sudden burst; the streak
                # catches a site that is simply blocked and will stay blocked,
                # which the window alone provably could not — see
                # _consecutive_blocked.
                burst = _recent_blocked_count(db, site)
                streak = _consecutive_blocked(db, site)
                if (burst >= CRAWL_FAIL_THRESHOLD or streak >= CRAWL_FAIL_THRESHOLD) \
                        and not _site_crawl_disabled(db, site):
                    _trip_breaker(db, site, max(burst, streak))

    finally:
        # Both crawlers close their HTTP client at the END of run(), so a crawl
        # that raises never closes it — and these crawlers fail routinely (FF.net
        # is permanently 403, AO3 returns 525s). Every failed scheduled crawl
        # leaked an httpx.AsyncClient and its connection pool, every few hours,
        # for as long as the process lived.
        if crawler is not None:
            try:
                await crawler.close()
            except Exception:
                pass


def _reap_orphaned_jobs() -> None:
    """Mark crawl jobs left as "running" by a previous process as failed.

    A crawl job only lives in the process that started it, so any row still
    marked running at startup belongs to a process that is gone — killed by a
    restart or a crash. They otherwise sit as "running" forever, showing a crawl
    in progress that is not, and skewing the recent-jobs list in the UI.
    """
    from datetime import timedelta
    from db.session import db_session
    from models.story import CrawlJob
    try:
        with db_session() as db:
            stale = (db.query(CrawlJob)
                     .filter(CrawlJob.status == "running")
                     .update({"status": "failed",
                              "error": "[transient] interrupted — process restarted",
                              "finished_at": datetime.now(timezone.utc)},
                             synchronize_session=False))
            if stale:
                logger.info(f"[scheduler] reaped {stale} interrupted crawl job(s)")
    except Exception as e:
        logger.warning(f"[scheduler] could not reap orphaned jobs: {e}")


def start_scheduler():
    """Register jobs and start the scheduler. Call once at app startup."""
    _reap_orphaned_jobs()

    # Feed polling is the reliable fresh-AO3 path. Runs on a schedule.
    FEED_INTERVAL_HOURS = float(os.getenv("FEED_INTERVAL_HOURS", "6"))
    scheduler.add_job(
        _run_feed_poll,
        trigger=IntervalTrigger(hours=FEED_INTERVAL_HOURS),
        id="poll_feeds",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # Direct crawlers (AO3/FFN). Registered unconditionally so the in-app settings
    # toggle can enable them at runtime without a restart — _run_crawl checks the
    # DB setting on each run and skips when disabled. NOTE: these are Cloudflare-
    # blocked from datacenter IPs (525/timeouts) and only work from a residential
    # IP, Tailscale exit node, or Cloudflare WARP.
    scheduler.add_job(
        _run_crawl, trigger=IntervalTrigger(hours=AO3_INTERVAL_HOURS),
        args=["ao3"], id="crawl_ao3", replace_existing=True,
        max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        _run_crawl, trigger=IntervalTrigger(hours=FFNET_INTERVAL_HOURS),
        args=["ffnet"], id="crawl_ffnet", replace_existing=True,
        max_instances=1, misfire_grace_time=300,
    )

    scheduler.start()

    # Record next run times
    for job_id, site in [("crawl_ao3", "ao3"), ("crawl_ffnet", "ffnet")]:
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            _next_run[site] = job.next_run_time

    if RUN_ON_STARTUP:
        logger.info("[scheduler] Running startup feed poll...")
        # Hold reference; bare create_task can be garbage-collected
        global _startup_task
        _startup_task = asyncio.create_task(_run_feed_poll())

    logger.info(
        f"[scheduler] Started — feed polling every {FEED_INTERVAL_HOURS}h, "
        f"direct crawl enabled: {os.getenv('ENABLE_DIRECT_CRAWL', 'false')}"
    )


# Fandoms to auto-poll feeds for. Override via TRACKED_FANDOMS env (comma-separated).
TRACKED_FANDOMS = [
    f.strip() for f in os.getenv(
        "TRACKED_FANDOMS",
        "Harry Potter - J. K. Rowling"
    ).split(",") if f.strip()
]


async def _run_feed_poll():
    """Poll AO3 Atom feeds for tracked fandoms and index new works."""
    from live_fetch.ao3_feeds import resolve_tag_id, fetch_feed, filter_entries
    from live_fetch.persist import persist_live_results
    from db.session import db_session
    from api.settings import get_setting
    import httpx

    # Pull the tracked fandom + filters from runtime settings
    fandoms = list(TRACKED_FANDOMS)
    min_words = max_words = None
    complete_only = False
    try:
        with db_session() as db:
            stored = get_setting(db, "tracked_fandom")
            if stored:
                fandoms = [f.strip() for f in stored.split(",") if f.strip()]
            try: min_words = int((get_setting(db, "feed_min_words") or "").strip() or 0) or None
            except Exception: pass
            try: max_words = int((get_setting(db, "feed_max_words") or "").strip() or 0) or None
            except Exception: pass
            complete_only = (get_setting(db, "feed_complete_only") or "false").lower() == "true"
    except Exception as e:
        logger.warning(f"[scheduler] Couldn't read settings: {e}")

    logger.info(
        f"[scheduler] Polling AO3 feeds for {len(fandoms)} fandom(s): {fandoms} "
        f"(filters: min_words={min_words}, max_words={max_words}, complete_only={complete_only})"
    )
    total_new = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": "FicAtlasBot/1.0 (+fanfic discovery)"},
        timeout=20, follow_redirects=True
    ) as client:
        for fandom in fandoms:
            try:
                tag_id = await resolve_tag_id(client, fandom)
                if not tag_id:
                    logger.warning(f"[scheduler] No feed for '{fandom}'")
                    continue
                await asyncio.sleep(4)
                entries = await fetch_feed(tag_id, limit=25)
                entries = filter_entries(
                    entries, min_words=min_words, max_words=max_words, complete_only=complete_only,
                )
                with db_session() as db:
                    new = persist_live_results(db, entries)
                total_new += new
                logger.info(f"[scheduler] {fandom}: {len(entries)} matched, {new} new")
                await asyncio.sleep(4)
            except Exception as e:
                logger.warning(f"[scheduler] Feed poll failed for '{fandom}': {e}")

    logger.info(f"[scheduler] Feed poll done — {total_new} new works indexed")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def get_schedule_status() -> dict:
    """Return current schedule info for the /api/crawl/schedule endpoint."""
    result = {}
    for site in ["ao3", "ffnet"]:
        job_id = f"crawl_{site}"
        job = scheduler.get_job(job_id)
        next_run = job.next_run_time if job else None
        result[site] = {
            "last_run": _last_run[site].isoformat() if _last_run[site] else None,
            "next_run": next_run.isoformat() if next_run else None,
            "interval_hours": AO3_INTERVAL_HOURS if site == "ao3" else FFNET_INTERVAL_HOURS,
        }
    return result
