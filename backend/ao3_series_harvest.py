r"""
Pull AO3's own series into the index.
=====================================

AO3 states series membership on every work listing blurb and work page. The
importer already parses those; a bug in ao3_series.record (ON CONFLICT against
a partial unique index without the WHERE clause) meant every save failed
silently, so the index held zero explicit series.

This script scrapes a few pages of popular fandom tag listings — twenty works
per page, series free on the blurb — and records whatever AO3 declares. It is
also the backfill for works we already hold: matching site_id to an existing
row attaches the series without re-importing the work.

    docker compose exec backend python ao3_series_harvest.py
    docker compose exec backend python ao3_series_harvest.py --fandom "Harry Potter - J. K. Rowling" --pages 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402 — needs the sys.path above
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text as sql_text

from db.session import db_session
from live_fetch.ao3_works_scraper import scrape_tag_works
import ao3_series

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Fandoms whose series readers actually look for. Harry Potter first because
# that is where "I searched the top series and barely any came up" was reported.
DEFAULT_FANDOMS = [
    "Harry Potter - J. K. Rowling",
    "Marvel",
    "Star Wars - All Media Types",
    "Supernatural (TV 2005)",
    "Bangtan Boys | BTS",
    "Original Work",
]


async def harvest(fandom: str, pages: int) -> tuple[int, int]:
    """Scrape `pages` of a fandom listing; return (series_links, works_attached)."""
    # revised_at rather than kudos: AO3's kudos-sorted listing for huge tags
    # has been observed returning an empty shell (HTTP 200, zero blurbs) while
    # the same tag sorted by update date returns a full page. Series ride along
    # on the blurb either way.
    result = await scrape_tag_works(tag=fandom, max_pages=pages, sort="revised_at")
    entries = result.get("entries") or []
    linked = attached = 0
    with db_session() as db:
        for e in entries:
            series = e.get("series") or []
            if not series:
                continue
            linked += len(series)
            site_id = str(e.get("site_id") or "")
            if not site_id:
                continue
            row = db.execute(sql_text("""
                SELECT id, author FROM stories
                WHERE site = 'ao3' AND site_id = :sid AND delisted_at IS NULL
                LIMIT 1
            """), {"sid": site_id}).first()
            if not row:
                continue
            n = ao3_series.record(db, str(row[0]), row[1] or e.get("author"), series)
            attached += n
        db.commit()
    return linked, attached


async def main_async(fandoms: list[str], pages: int) -> int:
    total_linked = total_attached = 0
    for fandom in fandoms:
        log.info(f"harvesting series from {fandom!r} ({pages} pages)…")
        try:
            linked, attached = await harvest(fandom, pages)
        except Exception as e:
            log.warning(f"  failed: {type(e).__name__}: {e}")
            continue
        log.info(f"  {linked} series links seen, {attached} attached to indexed works")
        total_linked += linked
        total_attached += attached
    log.info(f"DONE — {total_linked} links, {total_attached} attached")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull AO3 series from listing pages")
    ap.add_argument("--fandom", action="append", dest="fandoms",
                    help="AO3 canonical fandom tag (repeatable)")
    ap.add_argument("--pages", type=int, default=5,
                    help="listing pages per fandom (20 works each)")
    args = ap.parse_args()
    fandoms = args.fandoms or DEFAULT_FANDOMS
    return asyncio.run(main_async(fandoms, args.pages))


if __name__ == "__main__":
    sys.exit(main())
