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
from datetime import datetime, timezone
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
    """Re-check works that may have gained chapters, so the index is not a
    permanent snapshot of import day.

    Fanfiction is mutable: a WIP gains chapters, its word count grows, and it
    eventually completes. Three things already catch some of that —

      * _recent_works_loop walks tag pages sorted by revised_at, which IS AO3's
        update ordering, so anything updating in a TRACKED fandom surfaces on
        its own;
      * persist._enrich_existing applies updates forward-only whenever a work
        is re-encountered by any path;
      * the work-page harvest reads updated_at directly.

    — but none of them covers a work outside the tracked fandoms that nothing
    happens to re-encounter, and that is 5.48M in_progress AO3 rows.

    This used to target `is_hosted AND status='in_progress' AND site='ao3'`,
    which matches ZERO rows (only two AO3 works are hosted and both are
    complete), and to fetch through AO3 free-text search, which is
    robots-disallowed and returns nothing for an automated caller. It was a
    no-op on both counts.

    Now it re-reads the work page — permitted, authoritative, and the same
    request shape the harvest already makes — and picks WIPs by readership so
    the works someone will actually open are the ones kept current.
    """
    interval = _num("REFRESH_INTERVAL_MIN", 30) * 60
    batch = int(_num("REFRESH_BATCH", 40))
    delay = _num("REFRESH_DELAY", 2.0)
    from sqlalchemy import text as sql_text
    from db.session import db_session
    from models.story import Story
    from ao3_title_repair import RateLimiter, THROTTLED, fetch_work, apply_work, UA
    import httpx

    # Which WIP is worth a request right now?
    #
    # Ranking by popularity alone keeps re-reading the same well-read fic that
    # was abandoned in 2019, and never looks at the one that gained a chapter
    # last week. Three independent factors, multiplied:
    #
    #   activity  exp(-days_since_update / 365)
    #             A fic updated recently is very likely to update again; one
    #             untouched for years probably never will. Decays to 0.37 at a
    #             year and 0.05 at three, so a three-year-dormant work needs
    #             ~20x the readership to earn the same slot — it still gets
    #             checked eventually, just far less often. Falls back to
    #             published_at, and an unknown date is treated as a year old
    #             rather than as dead or as fresh.
    #
    #   reach     ln(1 + kudos + hits)
    #             Logarithmic on purpose: a 100k-hit fic matters more than a
    #             100-hit one, but not a thousand times more, or the head of
    #             the index would be all that ever got checked.
    #
    #   overdue   ln(2 + days_since_we_looked)
    #             Grows slowly and without bound, so nothing can be starved
    #             forever by works that merely score higher.
    #
    # And a nudge for works AO3 says are unfinished: chapter_count_total IS
    # NULL is the "12/?" form, an explicit statement that more is coming.
    STALE_SQL = sql_text("""
        WITH scored AS (
            SELECT id, site_id,
                   exp(-LEAST(
                       EXTRACT(EPOCH FROM (now() - COALESCE(updated_at, published_at,
                                                            now() - interval '365 days')))
                       / 86400.0, 3650) / 365.0)
                   * ln(1 + COALESCE(kudos,0) + COALESCE(hits,0))
                   * ln(2 + COALESCE(
                         EXTRACT(EPOCH FROM (now() - crawled_at)) / 86400.0, 3650))
                   * CASE WHEN chapter_count_total IS NULL THEN 1.3 ELSE 1.0 END
                   AS refresh_score
            FROM stories
            WHERE site = 'ao3'
              AND site_id ~ '^[0-9]+$'
              AND status = 'in_progress'
              AND (crawled_at IS NULL
                   OR crawled_at < now() - (:min_age || ' days')::interval)
        )
        SELECT id, site_id FROM scored
        ORDER BY refresh_score DESC NULLS LAST
        LIMIT :lim
    """)

    while True:
        try:
            def _stalest():
                with db_session() as db:
                    return db.execute(STALE_SQL, {
                        "lim": batch, "min_age": int(_num("REFRESH_MIN_AGE_DAYS", 7)),
                    }).fetchall()

            rows = await asyncio.to_thread(_stalest)
            if not rows:
                await asyncio.sleep(interval)
                continue

            limiter = RateLimiter(delay)
            stats: dict[str, int] = {}
            checked = 0

            def _run() -> int:
                nonlocal checked
                with httpx.Client(headers=UA) as client:
                    for sid, work_id in rows:
                        limiter.wait()
                        data = fetch_work(client, work_id, limiter)
                        if data is THROTTLED or not data:
                            continue
                        checked += 1
                        with db_session() as db:
                            story = db.query(Story).filter(Story.id == sid).first()
                            if not story:
                                continue
                            for field in apply_work(story, data):
                                stats[field] = stats.get(field, 0) + 1
                            story.crawled_at = datetime.now(timezone.utc)
                            db.commit()
                return checked

            await asyncio.to_thread(_run)
            changed = " ".join(f"{k}={v}" for k, v in sorted(stats.items(), key=lambda kv: -kv[1]))
            log.info(f"stale refresh: {checked}/{len(rows)} re-read — {changed or 'no changes'}")
        except Exception as e:
            log.warning(f"stale refresh failed: {type(e).__name__}: {e}")
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


async def _dlp_ratings(entries: list[dict]) -> None:
    """Attach DLP's community star rating to curated works we already hold."""
    import httpx
    from db.session import db_session
    from models.story import Story
    from api.library import _find_indexed_story
    from live_fetch.dlp_scraper import fetch_dlp_rating

    batch = int(_num("DLP_RATING_BATCH", 12))
    delay = _num("DLP_RATING_DELAY", 3.0)

    todo: list[tuple] = []
    with db_session() as db:
        for e in entries:
            thread = e.get("dlp_thread")
            cand = [v for k, v in (e.get("urls") or {}).items() if k in ("ffn", "ao3")]
            if not thread or not cand:
                continue
            hit = _find_indexed_story(db, cand)
            if hit is None:
                continue
            if any(str(t).startswith("dlp_stars:") for t in (hit.tags or [])):
                continue        # already rated
            todo.append((hit.id, thread))
            if len(todo) >= batch:
                break
    if not todo:
        return

    rated = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "FicAtlas/1.0 (personal fanfiction index)"},
        follow_redirects=True, timeout=60,
    ) as client:
        for story_id, thread in todo:
            value = await fetch_dlp_rating(client, thread)
            if value is not None:
                def _write():
                    with db_session() as db:
                        story = db.query(Story).filter(Story.id == story_id).first()
                        if story:
                            tags = list(story.tags or [])
                            tags = [t for t in tags if not str(t).startswith("dlp_stars:")]
                            tags.append(f"dlp_stars:{value:.2f}")
                            story.tags = tags
                            db.commit()
                await asyncio.to_thread(_write)
                rated += 1
            await asyncio.sleep(delay)
    log.info(f"dlp ratings: {rated}/{len(todo)} threads rated")


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

        # Star ratings live on the thread page, one request each, so they are
        # fetched a few per pass and only for works we hold and have not rated
        # yet. Stored as a tag rather than a column because the tag filter does
        # substring matching, which makes "dlp_stars:4" mean 4.00-4.99 for free.
        await _dlp_ratings(entries)
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


async def _listing_harvest_loop() -> None:
    """Fill AO3 metadata from tag-works listings, 20 works per request.

    See ao3_listing_harvest.py for why this exists: the work-page harvest costs
    one request per work, and a listing carries twenty. Same data, 20x fewer
    requests — which is both the only way the 13M-row summary gap is reachable
    and a straight reduction in load on AO3.

    Enrichment happens through persist_live_results, which fills gaps on rows we
    already hold and inserts anything new, so a pass both back-fills summaries
    and picks up works published since the dump.
    """
    from db.session import db_session
    from ao3_listing_harvest import next_fandoms, get_cursor, set_cursor, PAGES_PER_VISIT
    from live_fetch.ao3_works_scraper import scrape_tag_works
    from live_fetch.persist import persist_live_results

    interval = _num("LISTING_INTERVAL_MIN", 3) * 60
    # Alternate the two queues so neither starves the other. Backfill fills in
    # the works we already hold (where every missing summary is); discover finds
    # the works we do not (the post-2024 gap the dump cannot cover). Ranking by
    # either alone does one job well and the other not at all.
    mode = "backfill"

    while True:
        try:
            mode = "discover" if mode == "backfill" else "backfill"
            with db_session() as db:
                fandoms = next_fandoms(db, limit=int(_num("LISTING_FANDOMS", 40)), mode=mode)
            if not fandoms:
                log.info(f"listing[{mode}]: no fandoms in this queue yet "
                         "(run ao3_canonical_fandoms.py / refresh-facets)")
                await asyncio.sleep(interval)
                continue

            # One fandom per pass, rotating by how far behind each one is, so a
            # 25,000-page fandom cannot monopolise the queue.
            with db_session() as db:
                pending = sorted(
                    ((f, n, get_cursor(db, f, mode)) for f, n in fandoms),
                    key=lambda t: (t[2], -t[1]),      # least-walked, then biggest
                )
            fandom, works, start = pending[0]

            result = await scrape_tag_works(
                tag=fandom, max_pages=PAGES_PER_VISIT, start_page=start,
                sort="revised_at",
            )
            entries = result.get("entries") or []

            if result.get("pages_ok", 0) == 0 and result.get("pages_failed", 0) > 0:
                log.warning(f"listing {fandom[:40]}: all pages failed, cursor held")
                await asyncio.sleep(interval)
                continue

            saved = 0
            if entries:
                with db_session() as db:
                    saved = persist_live_results(db, entries)

            with db_session() as db:
                # Exhausted means we walked off the end; go round again next time
                # rather than stopping, since fandoms gain works continuously.
                set_cursor(db, fandom, 1 if result.get("exhausted")
                           else int(result.get("next_page", start + PAGES_PER_VISIT)),
                           mode)

            log.info(f"listing[{mode}] {fandom[:40]!r} ({works:,} works): pages "
                     f"{start}-{result.get('next_page', start) - 1}, "
                     f"{len(entries)} works, {saved} new")
        except Exception as e:
            log.warning(f"listing harvest failed: {type(e).__name__}: {e}")
        await asyncio.sleep(interval)


async def _wayback_cdx_loop() -> None:
    """Walk archive.org's CDX index for AO3 work URLs and queue what it finds.

    Discovery only — no work pages are fetched here. The walk is fast (one
    request yields tens of thousands of snapshot rows) and the fetch is slow, so
    keeping them in one loop would mean discovery finishing in hours and then
    sitting idle for months. See wayback_harvest.
    """
    from sqlalchemy import text as sql_text

    from db.session import db_session
    from api.settings import get_setting, put_setting
    from wayback_harvest import cdx_page, queue_ids

    KEY = "wayback_cdx_resume"
    interval = _num("WAYBACK_CDX_INTERVAL_SEC", 90)
    # Stop discovering once the queue is deep enough to keep the fetch loop busy
    # for weeks; there is no point holding 10M rows we will not reach this year,
    # and the CDX index is still there when we want more.
    high_water = int(_num("WAYBACK_QUEUE_MAX", 500_000))

    while True:
        try:
            with db_session() as db:
                pending = db.execute(sql_text(
                    "SELECT count(*) FROM wayback_queue WHERE done_at IS NULL")).scalar() or 0
                resume = get_setting(db, KEY) or None

            if pending >= high_water:
                await asyncio.sleep(interval * 20)
                continue

            pairs, next_key = await asyncio.to_thread(cdx_page, resume)

            with db_session() as db:
                added = queue_ids(db, pairs) if pairs else 0
                # No resume key means the walk reached the end of the index.
                # Start over: archive.org keeps capturing AO3, so a later pass
                # finds works that did not exist during the first.
                put_setting(db, KEY, next_key or "")
                if not next_key:
                    log.info("wayback cdx: index walked to the end, restarting")

            log.info(f"wayback cdx: {len(pairs):,} works in slice, "
                     f"{added:,} newly queued (pending {pending + added:,})")
        except Exception as e:
            log.warning(f"wayback cdx walk failed: {type(e).__name__}: {e}")
        await asyncio.sleep(interval)


async def _wayback_fetch_loop() -> None:
    """Fetch queued AO3 work pages from the Wayback Machine and persist them.

    This is the one AO3 enrichment path that puts no load on AO3 at all, so it
    is not gated by ao3_budget — pacing it against AO3's limiter would throw
    away the entire reason for using an archive mirror.
    """
    from db.session import db_session
    from live_fetch.persist import persist_live_results
    from wayback_harvest import (Transient, next_batch, mark_done, fetch_snapshot,
                                 queue_stats)

    batch = int(_num("WAYBACK_BATCH", 20))
    interval = _num("WAYBACK_INTERVAL_SEC", 15)
    # Consecutive unreachable works before giving up on this batch. Isolated
    # connection drops from archive.org are normal; a run of them means it is
    # genuinely unavailable and grinding through the rest is pointless.
    STALL_TOLERANCE = int(_num("WAYBACK_STALL_TOLERANCE", 3))

    while True:
        try:
            with db_session() as db:
                pending = next_batch(db, batch)
            if not pending:
                await asyncio.sleep(interval * 10)
                continue

            entries, results = [], []
            stalled = run = 0
            for work_id, ts in pending:
                try:
                    entry = await asyncio.to_thread(fetch_snapshot, work_id, ts)
                except Transient as e:
                    # Leave it queued — the work is fine, archive.org is not
                    # answering. Isolated drops are routine there, so skip this
                    # one and carry on; abandoning the batch on the first blip
                    # capped a pass at one or two works.
                    stalled += 1
                    run += 1
                    if run >= STALL_TOLERANCE:
                        log.info(f"wayback: {run} failures in a row ({e}), ending batch")
                        break
                    continue
                run = 0
                results.append((work_id, entry is not None))
                if entry:
                    entries.append(entry)

            counts: dict = {}
            if entries:
                with db_session() as db:
                    persist_live_results(db, entries, counts)
            # Mark outside the persist session: a row that fails to save is
            # still a snapshot we successfully read, and re-fetching it would
            # only fail the same way.
            with db_session() as db:
                for work_id, ok in results:
                    mark_done(db, work_id, ok)
                stats = queue_stats(db)

            log.info(f"wayback: {len(entries)}/{len(results)} parsed, "
                     f"{counts.get('saved', 0)} new, "
                     f"{counts.get('enriched', 0)} enriched, "
                     f"{stats['pending']:,} pending, "
                     f"interval {stats['budget']['interval']}s"
                     + (f" (backed off, {stalled} requeued)" if stalled else ""))
        except Exception as e:
            log.warning(f"wayback fetch failed: {type(e).__name__}: {e}")
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

    if _flag("LISTING_HARVEST", "true"):
        tasks.append(asyncio.create_task(_listing_harvest_loop()))
        log.info("AO3 listing harvest enabled (20 works per request)")

    if _flag("TITLE_REPAIR", "true"):
        tasks.append(asyncio.create_task(_title_repair_loop()))
        log.info("AO3 title repair enabled")

    if _flag("WAYBACK_HARVEST", "true"):
        tasks.append(asyncio.create_task(_wayback_cdx_loop()))
        tasks.append(asyncio.create_task(_wayback_fetch_loop()))
        log.info("Wayback harvest enabled (AO3 metadata at zero cost to AO3)")

    log.info("worker ready")
    # Idle forever; the scheduler runs on its own timers.
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
