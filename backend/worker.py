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
  REFRESH_STALE=true         re-check hosted WIPs so they don't stay frozen
  REFRESH_INTERVAL_MIN=60
  REFRESH_BATCH=40
  TITLE_REPAIR=true          repair AO3 titles the dump truncated (default on)
  TITLE_REPAIR_INTERVAL_MIN=1
  TITLE_REPAIR_BATCH=300
  TITLE_REPAIR_DELAY=2.0     target seconds between AO3 requests (widens on 429)
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
                        limit=pages * 20, pages=pages, automated=True,
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


async def _refresh_stale_loop() -> None:
    """Re-check works we already hold, stalest first, so the index isn't a
    permanent snapshot of import day.

    Fanfiction is mutable — a WIP gains chapters, changes word count, and
    eventually completes — but a row was frozen at the moment it was imported.
    Re-encounters now refresh (see persist._enrich_existing), and for the tracked
    fandoms that happens for free: the recent-works loop walks tag pages sorted
    by revised_at, which IS AO3's update feed, so anything that updates surfaces
    within the interval.

    This covers what that cannot: works outside the tracked fandoms. Refreshing
    all 10.7M in_progress rows individually is not feasible and would be rude to
    AO3, so it targets HOSTED works — the ones actually being read, ~30k rather
    than millions — oldest crawled_at first.
    """
    interval = _num("REFRESH_INTERVAL_MIN", 60) * 60
    batch = int(_num("REFRESH_BATCH", 40))
    from sqlalchemy import text as sql_text
    from db.session import db_session
    from live_fetch.ao3_live import fetch_live_ao3
    from live_fetch.persist import persist_live_results

    while True:
        try:
            def _stalest():
                with db_session() as db:
                    return db.execute(sql_text("""
                        SELECT title, author FROM stories
                        WHERE is_hosted AND status = 'in_progress'
                          AND site = 'ao3' AND title IS NOT NULL
                        ORDER BY crawled_at ASC NULLS FIRST
                        LIMIT :lim
                    """), {"lim": batch}).fetchall()

            rows = await asyncio.to_thread(_stalest)
            refreshed = 0
            for title, author in rows:
                try:
                    results = await fetch_live_ao3(
                        {"q": f"{title} {author or ''}".strip(), "status": None,
                         "sort": "relevance"}, limit=20, pages=1, automated=True)
                    if results:
                        def _save():
                            with db_session() as db:
                                return persist_live_results(db, results)
                        await asyncio.to_thread(_save)
                        refreshed += 1
                except Exception:
                    pass
                await asyncio.sleep(5)      # polite spacing between works
            if refreshed:
                log.info(f"refreshed {refreshed} stale hosted works")
        except Exception as e:
            log.warning(f"refresh pass failed: {type(e).__name__}: {e}")
        await asyncio.sleep(interval)


# ── Alternative archives: full, resumable import ─────────────────────────────
#
# The Open Doors archives were only ever reachable through the Library page's
# one-click buttons, which cap at 20 pages and always restart from page 1. The
# index held 21 HPFFA works, 0 HexFiles and 0 SquidgeWorld, so "search every
# archive at once" was true only for AO3, FFN and FicAlley.
#
# Those 21 rows were also 100% empty of metadata, because the works-listing
# parser silently dropped every field inside a collection listing — fixed
# separately in ao3_works_scraper.py. Re-walking them repairs them, since
# persist_live_results enriches rows it already has.
#
# This walks each of them end to end in the background instead. State is a page
# cursor in app_settings, so a restart resumes rather than re-reading page 1
# forever, and an archive that reports no "next" link is marked done and
# skipped from then on.
#
# The metadata is the good kind: an Otwarchive works listing carries summary,
# characters, relationships, freeform tags, warnings, word and chapter counts,
# language, kudos, comments, bookmarks, hits and the updated date — richer per
# work than any of the bulk dumps.
ARCHIVES: list[dict] = [
    {"name": "hpffa",        "tag": "hpffa_archive",
     "kwargs": {"tag": "", "collection": "hpfanfiction_hpff"}},
    {"name": "hexfiles",     "tag": "hexfiles_archive",
     "kwargs": {"tag": "", "collection": "harrypotterfanficarchive"}},
]

# SquidgeWorld is deliberately absent. It now sits behind an interactive bot
# challenge ("Making sure you're not a bot!") that returns a JS gate instead of
# a works listing, so a server-side scrape gets 0 works no matter how it is
# paged — which is why the index holds none of it. Retrying that on a timer
# would be pointless load on someone else's box. The Library page button
# remains for a human to try from a browser.
#
# Sizes are also far smaller than the archives they came from. Open Doors
# imports only carry the works whose authors opted in, and AO3 reports:
#     hpfanfiction_hpff        37 works  (HarryPotterFanfiction.com, ~85k live)
#     harrypotterfanficarchive 834 works (Harry Potter FanFic Archive, ~18k live)
# So a complete walk of both is ~871 works, not the tens of thousands the
# original sites held.


def _cursor_get(name: str) -> tuple[int, bool]:
    from api.settings import get_setting
    from db.session import db_session
    with db_session() as db:
        page = get_setting(db, f"archive_page:{name}")
        done = get_setting(db, f"archive_done:{name}")
    try:
        page_n = max(1, int(page))
    except (TypeError, ValueError):
        page_n = 1
    return page_n, str(done).lower() in ("1", "true", "yes")


def _cursor_put(name: str, page: int, done: bool) -> None:
    from api.settings import put_setting
    from db.session import db_session
    with db_session() as db:
        put_setting(db, f"archive_page:{name}", str(page))
        if done:
            put_setting(db, f"archive_done:{name}", "true")



def _dlp_import_missing(items: list[tuple[str, list[str]]], provenance: str) -> int:
    """Resolve DLP URLs we do not hold through FicHub and index them."""
    import httpx
    from db.session import db_session
    from fichub_meta import fetch_meta, HEADERS
    from live_fetch.persist import persist_live_results

    rows: list[dict] = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        for url, dlp_tags in items:
            meta = fetch_meta(client, url)
            if not meta:
                continue
            meta.setdefault("tags", [])
            # DLP's own curation tags are the point of the list — they are what
            # makes "recommended by DLP, tagged Time Travel" answerable.
            for t in list(dlp_tags) + [provenance]:
                if t and t not in meta["tags"]:
                    meta["tags"].append(t)
            rows.append(meta)

    if not rows:
        return 0
    with db_session() as db:
        return persist_live_results(db, rows)


async def _dlp_pass() -> None:
    """Merge DLP's curation onto stories already in the index, both corpora.

    DLP publishes two lists — HP (729 entries) and everything else (376) — and
    only 432 rows carried the tag, because the Library button defaults to the HP
    list with a limit and was evidently never run for the other one.

    This is the cheap half of the DLP flow deliberately: matching a curated
    entry to a story we already hold costs one indexed lookup and no network
    fetch per work, and it is what makes `dlp_library` usable as a quality
    filter. Pulling in the works we do NOT hold means a FicHub fetch each, which
    stays on the Library page's explicit button rather than running unattended.
    """
    from db.session import db_session
    from api.library import _find_indexed_story, _merge_dlp_tags, _record_cross_posts
    from live_fetch.dlp_scraper import fetch_dlp_library

    for corpus in ("hp", "other"):
        try:
            entries = await fetch_dlp_library(corpus=corpus, limit=None)
        except Exception as e:
            log.warning(f"dlp {corpus}: fetch failed: {type(e).__name__}: {e}")
            continue
        if not entries:
            continue
        tagged = 0
        with db_session() as db:
            for e in entries:
                cand = [v for k, v in (e.get("urls") or {}).items() if k in ("ffn", "ao3")]
                if not cand:
                    continue
                hit = _find_indexed_story(db, cand)
                if hit is None:
                    continue
                before = set(hit.tags or [])
                _merge_dlp_tags(hit, e.get("dlp_tags") or [])
                _record_cross_posts(hit, cand)
                if set(hit.tags or []) != before:
                    tagged += 1
            db.commit()
        # Then ADD the curated works we do not hold at all.
        #
        # Metadata only, via FicHub's /api/v0/meta — which resolves FFN URLs we
        # cannot reach ourselves, and produces no download. Full text is a
        # separate matter: /api/v0/epub hands back a /cache/epub/... URL and
        # FicHub's robots.txt disallows that path, so EPUB fetching stays on the
        # Library page's explicit button rather than running unattended here.
        missing = []
        with db_session() as db:
            for e in entries:
                cand = [v for k, v in (e.get("urls") or {}).items() if k in ("ffn", "ao3")]
                if cand and _find_indexed_story(db, cand) is None:
                    missing.append((cand[0], e.get("dlp_tags") or []))
        if missing:
            batch = int(_num("DLP_IMPORT_BATCH", 25))
            added = await asyncio.to_thread(_dlp_import_missing, missing[:batch], provenance="dlp_library")
            log.info(f"dlp {corpus}: {len(missing)} not indexed, {added} added this pass")
        log.info(f"dlp {corpus}: {len(entries)} curated, {tagged} newly tagged")
        await asyncio.sleep(5)


async def _archives_loop() -> None:
    """Walk each alternative archive to completion, a few pages at a time."""
    from db.session import db_session
    from live_fetch.ao3_works_scraper import scrape_tag_works
    from live_fetch.persist import persist_live_results

    # Pages per pass is small and the gap between passes long, because these are
    # the same Otwarchive endpoints the title repair already competes for and
    # AO3 rate-limits harder than its robots.txt implies (see ao3_title_repair).
    pages = int(_num("ARCHIVES_PAGES", 4))
    interval = _num("ARCHIVES_INTERVAL_MIN", 12) * 60

    while True:
        for arch in ARCHIVES:
            name, provenance = arch["name"], arch["tag"]
            try:
                start, done = _cursor_get(name)
                if done:
                    continue
                log.info(f"archive {name}: pages {start}-{start + pages - 1}")
                result = await scrape_tag_works(
                    max_pages=pages, start_page=start,
                    sort="revised_at", **arch["kwargs"],
                )
                entries = result.get("entries") or []

                # All pages failing is a transport problem, not the end of the
                # archive — leave the cursor alone so the next pass retries the
                # same range instead of skipping past it.
                if result.get("pages_ok", 0) == 0 and result.get("pages_failed", 0) > 0:
                    log.warning(f"archive {name}: all pages failed, cursor held at {start}")
                    continue

                for e in entries:
                    e.setdefault("tags", [])
                    if provenance not in e["tags"]:
                        e["tags"].append(provenance)

                saved = 0
                if entries:
                    with db_session() as db:
                        saved = persist_live_results(db, entries)

                exhausted = bool(result.get("exhausted"))
                _cursor_put(name, int(result.get("next_page", start + pages)), exhausted)
                log.info(f"archive {name}: {len(entries)} works, {saved} saved, "
                         f"next page {result.get('next_page')}"
                         + (" — COMPLETE" if exhausted else ""))
            except Exception as e:
                log.warning(f"archive {name} pass failed: {type(e).__name__}: {e}")
            await asyncio.sleep(20)

        # DLP is a flat curated list rather than a paginated archive, so it gets
        # a full re-check each cycle instead of a page cursor. It is two HTTP
        # requests and the tagging is idempotent.
        try:
            await _dlp_pass()
        except Exception as e:
            log.warning(f"dlp pass failed: {type(e).__name__}: {e}")

        await asyncio.sleep(interval)


async def _title_repair_loop() -> None:
    """Repair AO3 titles the bulk dump truncated mid-phrase.

    The dump ships cut titles — "Harry Potter and" for a work actually called
    "Harry Potter and Homosexual Rights Feat. Severus Snape". 398,817 AO3 rows
    end on a dangling and/of/the/with, which cannot end a real title, so those
    are identifiable with confidence. Cuts landing on a content word are
    indistinguishable from a short title and get fixed only if a live fetch
    happens to re-encounter the work.

    One request per work, so this is paced to grind slowly rather than finish.
    """
    # Pacing, arrived at by measurement after two wrong guesses.
    #
    # AO3's robots.txt sets no Crawl-delay for `*` and does not disallow work
    # pages, which is NOT the same as having no limit — they enforce one they
    # do not publish. Running 8 connections at ~0.87 req/s drew 76 HTTP 429s in
    # a single 300-work pass, against zero for the serial version.
    #
    # The second wrong guess was that concurrency would help. It does not.
    # Complete passes, works processed per second of working time:
    #
    #     serial, 1 conn                 100 works / 525s  = 5.25s per work
    #     4 conns + adaptive limiter     300 works / 1724s = 5.75s per work
    #
    # Once you back off to a rate AO3 tolerates, its limit binds BELOW where
    # latency was binding, so overlapping the waiting has nothing left to
    # recover. The pool is kept anyway because the limiter is what stopped the
    # 429 storm and it self-corrects, but it is not what made this faster.
    #
    # What made it faster was deleting the idle gap. The old config did 100
    # works in ~9 minutes and then slept 45, so ~84% of the wall clock was
    # spent doing nothing:
    #
    #     old (100/pass, 45min gap)   32.3s per work   ~149 days for 398,751
    #     now (300/pass, 1min gap)     5.95s per work   ~27 days
    #
    # Deliberately not faster. The measured pass still takes 3 x 429 at this
    # rate, and the limiter widens itself when it does.
    delay = _num("TITLE_REPAIR_DELAY", 2.0)
    interval = _num("TITLE_REPAIR_INTERVAL_MIN", 1) * 60
    batch = int(_num("TITLE_REPAIR_BATCH", 300))
    from ao3_title_repair import run as repair_run

    while True:
        try:
            log.info(f"AO3 title repair pass ({batch} works @ {delay}s)")
            await asyncio.to_thread(repair_run, batch, False, delay)
        except Exception as e:
            log.warning(f"title repair pass failed: {type(e).__name__}: {e}")
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

    if _flag("REFRESH_STALE"):
        tasks.append(asyncio.create_task(_refresh_stale_loop()))
        log.info("stale-work refresh enabled")

    if _flag("ARCHIVES_IMPORT", "true"):
        tasks.append(asyncio.create_task(_archives_loop()))
        log.info("alternative-archive full import enabled (HPFFA + HexFiles)")

    if _flag("TITLE_REPAIR", "true"):
        tasks.append(asyncio.create_task(_title_repair_loop()))
        log.info("AO3 title repair enabled")

    log.info("worker ready")
    # Idle forever; the scheduler runs on its own timers.
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
