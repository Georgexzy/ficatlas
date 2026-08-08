r"""
Backfill AO3 series from work pages of popular indexed works.
=============================================================

Listing-page harvest is flaky on huge tags (AO3 sometimes returns an empty
shell). Work pages always carry the series block when one exists, and we
already hold the site_ids of the most-read works — so fetch those pages
directly and record what they declare.

    docker compose exec backend python ao3_series_backfill.py
    docker compose exec backend python ao3_series_backfill.py --fandom "Harry Potter" --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

from sqlalchemy import text as sql_text

from db.session import db_session
from live_fetch.ao3_works_scraper import HEADERS, AO3_TIMEOUT
import ao3_series

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


async def backfill(fandom_substr: str, limit: int, min_kudos: int) -> int:
    import httpx

    with db_session() as db:
        rows = db.execute(sql_text("""
            SELECT id, site_id, author, title FROM stories
            WHERE site = 'ao3' AND delisted_at IS NULL
              AND site_id ~ '^[0-9]+$'
              AND EXISTS (
                SELECT 1 FROM unnest(fandoms) f
                WHERE f ILIKE :pat)
              AND COALESCE(kudos, 0) >= :k
            ORDER BY kudos DESC NULLS LAST
            LIMIT :n
        """), {"pat": f"%{fandom_substr}%", "k": min_kudos, "n": limit}).fetchall()

    log.info(f"fetching series from {len(rows)} popular AO3 works "
             f"(fandom ~{fandom_substr!r}, kudos>={min_kudos})")
    attached = 0
    async with httpx.AsyncClient(
            headers=HEADERS, timeout=AO3_TIMEOUT, follow_redirects=True) as client:
        with db_session() as db:
            for i, (sid, site_id, author, title) in enumerate(rows, 1):
                url = f"https://archiveofourown.org/works/{site_id}"
                try:
                    r = await client.get(url, params={"view_adult": "true"})
                except Exception as e:
                    log.warning(f"  fetch failed {site_id}: {type(e).__name__}: {e}")
                    continue
                if r.status_code != 200:
                    log.warning(f"  HTTP {r.status_code} for {site_id}")
                    continue
                entries = ao3_series.parse_series(r.text)
                if not entries:
                    continue
                n = ao3_series.record(db, str(sid), author, entries)
                attached += n
                names = ", ".join(e["name"] for e in entries)
                log.info(f"  {i}/{len(rows)} {(title or '')[:48]!r} → {names}")
                if i % 10 == 0:
                    db.commit()
                    await asyncio.sleep(1.2)
            db.commit()
    log.info(f"DONE — attached {attached} series memberships")
    return attached


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fandom", default="Harry Potter")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--min-kudos", type=int, default=3000)
    args = ap.parse_args()
    asyncio.run(backfill(args.fandom, args.limit, args.min_kudos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
