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

scheduler = AsyncIOScheduler(timezone="UTC")
_last_run: dict[str, datetime | None] = {"ao3": None, "ffnet": None}
_next_run: dict[str, datetime | None] = {"ao3": None, "ffnet": None}


async def _run_crawl(site: str):
    """Wrapper that records timing and logs progress."""
    from crawlers.ao3 import AO3Crawler
    from crawlers.ffnet import FFNetCrawler
    from models.story import CrawlJob, SiteEnum
    from db.session import db_session

    CRAWLERS = {"ao3": AO3Crawler, "ffnet": FFNetCrawler}
    if site not in CRAWLERS:
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
        logger.error(f"[scheduler] {site} crawl failed: {e}")
        with db_session() as db:
            from models.story import CrawlJob as CJ
            import uuid
            j = db.query(CJ).filter(CJ.id == uuid.UUID(job_id)).first()
            if j:
                j.status = "failed"
                j.error  = str(e)
                j.finished_at = datetime.now(timezone.utc)


def start_scheduler():
    """Register jobs and start the scheduler. Call once at app startup."""

    scheduler.add_job(
        _run_crawl,
        trigger=IntervalTrigger(hours=AO3_INTERVAL_HOURS),
        args=["ao3"],
        id="crawl_ao3",
        replace_existing=True,
        max_instances=1,        # never overlap
        misfire_grace_time=300, # ok to be 5 min late
    )

    scheduler.add_job(
        _run_crawl,
        trigger=IntervalTrigger(hours=FFNET_INTERVAL_HOURS),
        args=["ffnet"],
        id="crawl_ffnet",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.start()

    # Record next run times
    for job_id, site in [("crawl_ao3", "ao3"), ("crawl_ffnet", "ffnet")]:
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            _next_run[site] = job.next_run_time

    if RUN_ON_STARTUP:
        logger.info("[scheduler] Running startup crawls...")
        asyncio.create_task(_run_crawl("ao3"))
        asyncio.create_task(_run_crawl("ffnet"))

    logger.info(
        f"[scheduler] Started — AO3 every {AO3_INTERVAL_HOURS}h, "
        f"FF.net every {FFNET_INTERVAL_HOURS}h, "
        f"run on startup: {RUN_ON_STARTUP}"
    )


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
