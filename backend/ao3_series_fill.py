r"""Fetch the works a series contains, for series we only partly hold.

The gap this closes
-------------------
AO3 positions are the AUTHOR'S numbering and we record them faithfully, so a
series where this index happens to hold works 7, 8 and 9 renders a list that
starts at 7. Measured on this database: 42,563 series have no work at position 1,
21,768 are a single work numbered above 1, and 15,325 have interior gaps. None of
that is corruption — it is the author saying "this is the 7th" about a work whose
siblings were never indexed.

Explaining that on the page (see SeriesClient) is honest but unsatisfying: the
reader wants the other works, not a note about why they are missing. This fetches
them.

Why it can work at all
----------------------
109,398 series carry an `ao3_id`, recorded from the series block on work pages by
ao3_series.py. AO3 publishes the members at /series/<id> using the same work-blurb
markup as a tag listing, which live_fetch.ao3_works_scraper already parses — so
this module is mostly wiring, not new parsing.

    docker compose exec backend python ao3_series_fill.py --limit 5 --dry-run
    docker compose exec backend python ao3_series_fill.py --limit 200

Politeness
----------
Every fetch goes through _get_with_fallback, so it inherits the shared ao3_budget
pacing, the 525 retries and the cooldown that the rest of the AO3 crawling uses.
One series is one request for most series (AO3 paginates at 20 works), and the
worker loop runs a small batch at a time — the point is to close the gap steadily,
not to re-crawl AO3.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text as sql_text

log = logging.getLogger("ao3_series_fill")

# AO3 shows 20 works per series page. Most series are one page; a handful are
# enormous. Capped so a single pathological series cannot monopolise a batch.
MAX_PAGES = 5


def incomplete_series(db, limit: int) -> list[tuple[str, str, str | None]]:
    """(series_id, ao3_id, name) for series we hold only part of.

    "Part of" means the works we have do not start at 1, or have gaps between
    them. A series whose held works run 1..N contiguously is complete as far as
    anyone can tell from here, and is not worth a request.

    Ordered by how much is missing, so the worst pages improve first.
    """
    rows = db.execute(sql_text("""
        SELECT se.id, se.ao3_id, se.name,
               max(sw.position) - min(sw.position) + 1 - count(*) AS gaps,
               min(sw.position) - 1                              AS missing_before
        FROM series se
        JOIN series_works sw ON sw.series_id = se.id
        WHERE se.ao3_id IS NOT NULL
        GROUP BY se.id, se.ao3_id, se.name
        HAVING min(sw.position) > 1
            OR (max(sw.position) - min(sw.position) + 1) <> count(*)
        ORDER BY (max(sw.position) - min(sw.position) + 1 - count(*))
               + (min(sw.position) - 1) DESC
        LIMIT :lim
    """), {"lim": limit}).fetchall()
    return [(str(r[0]), r[1], r[2]) for r in rows]


async def fetch_series_works(ao3_id: str) -> list[dict]:
    """Every work AO3 lists for a series, in the order it lists them.

    That order IS the position: AO3 renders a series in the author's sequence, so
    the nth blurb is part n. Taking it from the listing rather than from each work
    page is what makes this one request instead of twenty.
    """
    import httpx
    from live_fetch.ao3_feeds import _get_with_fallback, HEADERS, AO3_TIMEOUT
    from live_fetch.ao3_works_scraper import parse_works_page

    # `ao3_id` is stored as "ao3:1027363" by ao3_series.record.
    bare = ao3_id.split(":", 1)[-1]
    out: list[dict] = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=AO3_TIMEOUT,
                                 follow_redirects=True) as client:
        for page in range(1, MAX_PAGES + 1):
            path = f"/series/{bare}?page={page}"
            r = await _get_with_fallback(client, path)
            if r is None or r.status_code != 200:
                break
            # parse_works_page returns (entries, has_next) — NOT a list. Treating
            # it as one silently "found" 2 works in every series, because that is
            # the length of a 2-tuple, and the numbers looked plausible enough to
            # nearly ship.
            entries, has_next = parse_works_page(r.text, page=page)
            if not entries:
                break
            out.extend(entries)
            # Otwarchive's own next-page marker, which is what the scraper uses;
            # a "fewer than 20 means last page" guess is wrong for a series whose
            # final page happens to hold exactly 20.
            if not has_next:
                break
    return out


def fill_one(db, series_id: str, ao3_id: str, entries: list[dict]) -> tuple[int, int]:
    """Index any works we lack and record their positions. -> (indexed, linked)"""
    from live_fetch.persist import persist_live_results
    import ao3_series

    if not entries:
        return (0, 0)

    name = db.execute(sql_text("SELECT name FROM series WHERE id = :s"),
                      {"s": series_id}).scalar() or ""

    indexed = persist_live_results(db, entries)

    linked = 0
    for i, e in enumerate(entries, start=1):
        url = e.get("url")
        if not url:
            continue
        story_id = db.execute(sql_text(
            "SELECT id FROM stories WHERE url = :u LIMIT 1"), {"u": url}).scalar()
        if not story_id:
            continue
        # Position comes from the LISTING ORDER, which is the author's sequence.
        linked += ao3_series.record(
            db, str(story_id), e.get("author"),
            [{"position": i, "ao3_id": ao3_id.split(":", 1)[-1], "name": name}])

    # Tidy up anything the page did not account for.
    #
    # A work can be in our copy of a series with an implausible position (a year,
    # from the cue parser before it was bounded) and no longer be listed on AO3 —
    # the author removed it, or it sits past the page cap. Linking the works that
    # ARE listed leaves those rows behind, so a series comes back as
    # "1,2,3,4,5,6,2024" and still reads as broken.
    #
    # They are appended after the real sequence rather than deleted: the work is
    # genuinely associated with the series, we simply no longer know where. An
    # order derived from publication date beats leaving a year in the list, and
    # beats dropping a work the reader might want.
    db.execute(sql_text("""
        WITH stale AS (
            SELECT sw.story_id,
                   row_number() OVER (ORDER BY COALESCE(st.published_at, st.updated_at,
                                                        TIMESTAMPTZ '1970-01-01'),
                                               sw.story_id) AS rn
            FROM series_works sw
            JOIN stories st ON st.id = sw.story_id
            WHERE sw.series_id = :s AND sw.position > :maxpos
        )
        UPDATE series_works sw
           SET position = :maxpos + stale.rn
          FROM stale
         WHERE sw.series_id = :s AND sw.story_id = stale.story_id
    """), {"s": series_id, "maxpos": len(entries)})
    return (indexed, linked)


async def run(limit: int = 50, dry_run: bool = False) -> dict:
    from db.session import db_session

    with db_session() as db:
        targets = incomplete_series(db, limit)

    stats = {"series": 0, "indexed": 0, "linked": 0, "empty": 0}
    for series_id, ao3_id, name in targets:
        try:
            entries = await fetch_series_works(ao3_id)
        except Exception as e:
            log.warning("series %s fetch failed: %s: %s", ao3_id, type(e).__name__, e)
            continue
        if not entries:
            stats["empty"] += 1
            continue
        if dry_run:
            log.info("would fill %-40s %s -> %d works listed",
                     (name or "")[:40], ao3_id, len(entries))
            stats["series"] += 1
            continue
        with db_session() as db:
            ix, lk = fill_one(db, series_id, ao3_id, entries)
        stats["series"] += 1
        stats["indexed"] += ix
        stats["linked"] += lk
        log.info("filled %-40s +%d indexed, %d linked", (name or "")[:40], ix, lk)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Fetch missing works for partly-held AO3 series.")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(asyncio.run(run(limit=a.limit, dry_run=a.dry_run)))
