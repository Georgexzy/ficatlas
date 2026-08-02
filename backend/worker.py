"""
FicAtlas background worker.
===========================

Everything recurring that isn't a web request: the AO3 feed poll, the scheduled
crawls, and slow backfills.

Why a separate process rather than threads inside the API:

  * Heavy work competed with request handling. The scheduler ran in the API's own
    event loop, so a feed poll or crawl blocked the same loop serving searches.
  * Ad-hoc batches (`docker compose exec backend python …`) died whenever the API
    container restarted. That killed a dedup run and an enrichment run partway
    through during development — uvicorn's --reload restarts on any file change.

The API no longer starts the scheduler at all (it checks RUN_SCHEDULER), so
adding this container does not double up the polling.

Backfills are opt-in and paced deliberately. They exist to fill gaps slowly over
days, not to saturate the database or hammer archive.org:

  ENRICH_FFNET=true          walk FF.net stories missing metadata, via Wayback
  ENRICH_BATCH=200           stories per pass
  ENRICH_INTERVAL_MIN=30     minutes between passes
  DEDUP_CROSSPOSTS=true      merge newly-imported cross-posted duplicates
  DEDUP_INTERVAL_MIN=180
  RECENT_WORKS=true          index AO3 works newer than the bulk dump (default on)
  RECENT_INTERVAL_MIN=20
  RECENT_PAGES=3
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
)
log = logging.getLogger("worker")


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


async def _enrich_loop() -> None:
    """Backfill FF.net genres/characters/engagement from the Wayback Machine.

    One HTTP request per story against archive.org, so this is paced to run
    forever in the background rather than to finish quickly. Each pass is small
    and the whole thing is idempotent — it only ever selects stories that still
    have no characters.
    """
    batch = int(_num("ENRICH_BATCH", 200))
    interval = _num("ENRICH_INTERVAL_MIN", 30) * 60
    from ffnet_enrich import run as enrich_run

    while True:
        try:
            log.info(f"FF.net enrichment pass ({batch} stories)")
            await asyncio.to_thread(enrich_run, batch, False, 0.5, 25)
        except Exception as e:
            log.warning(f"enrichment pass failed: {type(e).__name__}: {e}")
        await asyncio.sleep(interval)


async def _dedup_loop() -> None:
    """Merge cross-posted duplicates that arrive with new imports.

    Bounded per pass: merge_group deletes rows, so a small, frequent, restartable
    batch is much safer than one long sweep.
    """
    interval = _num("DEDUP_INTERVAL_MIN", 180) * 60
    from db.session import db_session
    from live_fetch.crosspost import group_existing, merge_group

    while True:
        try:
            def _pass() -> int:
                merged = 0
                with db_session() as db:
                    for group in group_existing(db, limit=2000):
                        try:
                            merge_group(db, group)
                            db.commit()
                            merged += len(group) - 1
                        except Exception:
                            db.rollback()
                return merged

            n = await asyncio.to_thread(_pass)
            if n:
                log.info(f"cross-post dedup merged {n} duplicate rows")
        except Exception as e:
            log.warning(f"dedup pass failed: {type(e).__name__}: {e}")
        await asyncio.sleep(interval)


async def _recent_works_loop() -> None:
    """Walk AO3 tag pages for the tracked fandoms and index what's there.

    This is the ONLY way to get works published after the bulk dump. That dump
    tops out at AO3 work id 63,178,258 with zero entries above 70M, while AO3 is
    now issuing ids around 89.7M — so everything from roughly mid-2024 onward is
    missing and no amount of re-importing will produce it.

    It accumulates rather than backfills in bulk: ~20 works per page at a few
    seconds each. Over days that is a real dent in the recent end; it will never
    be millions, and pretending otherwise would just mean hammering AO3.

    Uses the tag endpoint deliberately — measured 2/2 successful at 4-5s, versus
    /works/search at 1/2 and 29s when it worked.
    """
    interval = _num("RECENT_INTERVAL_MIN", 20) * 60
    pages = int(_num("RECENT_PAGES", 3))
    from db.session import db_session
    from api.settings import get_setting
    from live_fetch.ao3_live import fetch_live_ao3
    from live_fetch.persist import persist_live_results

    while True:
        try:
            with db_session() as db:
                tracked = get_setting(db, "tracked_fandom") or ""
            fandoms = [f.strip() for f in tracked.split(",") if f.strip()]
            for fandom in fandoms:
                try:
                    results = await fetch_live_ao3(
                        {"fandoms": fandom, "status": None, "sort": "updated_desc"},
                        limit=pages * 20, pages=pages,
                    )
                    if results:
                        def _save():
                            with db_session() as db:
                                return persist_live_results(db, results)
                        new = await asyncio.to_thread(_save)
                        log.info(f"recent works: {fandom} — {len(results)} fetched, {new} new")
                except Exception as e:
                    log.warning(f"recent works failed for {fandom}: {type(e).__name__}: {e}")
                await asyncio.sleep(10)   # be polite between fandoms
        except Exception as e:
            log.warning(f"recent-works pass failed: {type(e).__name__}: {e}")
        await asyncio.sleep(interval)


async def main() -> None:
    # Schema/indexes may not exist yet on a first boot; the API does this too and
    # it is idempotent, so whichever wins the race is fine.
    try:
        from init_db import init as init_db
        init_db()
    except Exception as e:
        log.warning(f"DB init failed (continuing): {e}")

    tasks: list[asyncio.Task] = []

    if _flag("RUN_SCHEDULER", "true"):
        from scheduler import start_scheduler
        start_scheduler()
        log.info("scheduler started (feed polls + crawls)")

    if _flag("ENRICH_FFNET"):
        tasks.append(asyncio.create_task(_enrich_loop()))
        log.info("FF.net enrichment backfill enabled")

    if _flag("DEDUP_CROSSPOSTS"):
        tasks.append(asyncio.create_task(_dedup_loop()))
        log.info("cross-post dedup enabled")

    if _flag("RECENT_WORKS", "true"):
        tasks.append(asyncio.create_task(_recent_works_loop()))
        log.info("recent-works indexing enabled (post-dump AO3 coverage)")

    log.info("worker ready")
    # Idle forever; the scheduler runs on its own timers.
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
