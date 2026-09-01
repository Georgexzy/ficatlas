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
import os
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text as sql_text

log = logging.getLogger("ao3_series_fill")

# AO3 shows 20 works per series page. Most series are one page; a handful are
# enormous. Capped so a single pathological series cannot monopolise a batch.
MAX_PAGES = int(os.getenv("SERIES_FILL_MAX_PAGES", "10"))

# How long before a series is worth another look. Nothing recorded an attempt,
# so the ranking -- which favours the series missing the MOST works -- handed
# back the same five every cycle forever. Every one of the current top targets
# needs more than MAX_PAGES * 20 positions, so none of them can ever be
# completed, and the loop spent ~25 AO3 requests every 15 minutes achieving
# nothing while the 42,563 series it was written for were never reached.
RETRY_AFTER_DAYS = float(os.getenv("SERIES_FILL_RETRY_DAYS", "30"))


def incomplete_series(db, limit: int) -> list[tuple[str, str, str | None]]:
    """(series_id, ao3_id, name) for series we hold only part of.

    "Part of" means the works we have do not start at 1, or have gaps between
    them. A series whose held works run 1..N contiguously is complete as far as
    anyone can tell from here, and is not worth a request.

    Ordered by how much is missing, so the worst pages improve first -- but
    only among series not tried recently. Without that clause the ordering is
    deterministic and the un-fixable sort to the top permanently.
    """
    # This aggregates the whole series/series_works join, and db/session.py
    # applies a 60s statement timeout by default. At ~340ms today that is not
    # close, but the margin shrinks as the table grows and the failure mode is
    # not a slow cycle -- it is the whole fill loop raising and logging a
    # warning once a week while nothing gets filled. Give the one statement
    # room; if it ever needs minutes, it wants a queue table like
    # ao3_refresh_queue rather than a longer timeout.
    db.execute(sql_text("SET LOCAL statement_timeout = '300s'"))
    rows = db.execute(sql_text("""
        SELECT se.id, se.ao3_id, se.name,
               max(sw.position) - min(sw.position) + 1 - count(*) AS gaps,
               min(sw.position) - 1                              AS missing_before
        FROM series se
        JOIN series_works sw ON sw.series_id = se.id
        LEFT JOIN series_fill_log fl ON fl.series_id = se.id
        WHERE se.ao3_id IS NOT NULL
          AND (fl.attempted_at IS NULL
               OR fl.attempted_at < now() - (:retry_days * INTERVAL '1 day'))
        GROUP BY se.id, se.ao3_id, se.name
        HAVING min(sw.position) > 1
            OR (max(sw.position) - min(sw.position) + 1) <> count(*)
        ORDER BY (max(sw.position) - min(sw.position) + 1 - count(*))
               + (min(sw.position) - 1) DESC
        LIMIT :lim
    """), {"lim": limit, "retry_days": RETRY_AFTER_DAYS}).fetchall()
    return [(str(r[0]), r[1], r[2]) for r in rows]


def record_attempt(db, series_id: str, complete: bool, listed: int) -> None:
    """Mark a series tried, so the queue advances whatever the outcome.

    Recorded for every outcome including failure -- a series AO3 404s, or one
    larger than the page cap, is exactly the case that otherwise re-ranks to
    the head forever.
    """
    db.execute(sql_text("""
        INSERT INTO series_fill_log (series_id, attempted_at, complete, listed)
        VALUES (:s, now(), :c, :n)
        ON CONFLICT (series_id) DO UPDATE
           SET attempted_at = now(), complete = :c, listed = :n
    """), {"s": series_id, "c": complete, "n": listed})


async def fetch_series_works(ao3_id: str) -> tuple[list[dict], bool]:
    """Every work AO3 lists for a series -> (entries, complete).

    That order IS the position: AO3 renders a series in the author's sequence, so
    the nth blurb is part n. Taking it from the listing rather than from each work
    page is what makes this one request instead of twenty.

    `complete` says whether the listing was walked to its end. It matters
    because the caller renumbers everything past the last entry it saw: a 525
    or a rate-limit on page 2 of a 60-work series used to look exactly like a
    complete 20-work series, and the sweep would then rewrite the author's own
    positions for works 21..60 into publication order. That is unrecoverable —
    the stated positions are gone — and one network blip was enough to cause
    it.
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
                return out, False       # fetch failed part-way: not complete
            # parse_works_page returns (entries, has_next) — NOT a list. Treating
            # it as one silently "found" 2 works in every series, because that is
            # the length of a 2-tuple, and the numbers looked plausible enough to
            # nearly ship.
            entries, has_next = parse_works_page(r.text, page=page)
            if not entries:
                return out, False
            out.extend(entries)
            # Otwarchive's own next-page marker, which is what the scraper uses;
            # a "fewer than 20 means last page" guess is wrong for a series whose
            # final page happens to hold exactly 20.
            if not has_next:
                return out, True
    # Ran out of page budget with more to fetch. The works past the cap are
    # real and correctly positioned; saying "complete" here would renumber them.
    return out, False


def fill_one(db, series_id: str, ao3_id: str, entries: list[dict],
             complete: bool = False) -> tuple[int, int]:
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
    # ONLY when the listing was walked to its end. Everything below rewrites
    # positions past `maxpos`, and a short listing makes `maxpos` a lie.
    if not complete:
        return (indexed, linked)

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


def _in_session(fn):
    """Run one unit of blocking DB work in its own session.

    Every DB call in run() goes through here and through asyncio.to_thread,
    because run() is awaited on the worker's single event loop. psycopg2 is
    blocking, so calling it inline stalled every other loop in the worker --
    the crawlers, the refresh queue, the AO3 pacing budget -- for as long as a
    GROUP BY over series_works plus a per-work lookup takes. Every other loop
    in worker.py already does this; this one did not.
    """
    from db.session import db_session
    with db_session() as db:
        return fn(db)


async def run(limit: int = 50, dry_run: bool = False) -> dict:
    targets = await asyncio.to_thread(_in_session, lambda db: incomplete_series(db, limit))

    stats = {"series": 0, "indexed": 0, "linked": 0, "empty": 0}
    for series_id, ao3_id, name in targets:
        try:
            entries, complete = await fetch_series_works(ao3_id)
        except Exception as e:
            log.warning("series %s fetch failed: %s: %s", ao3_id, type(e).__name__, e)
            if not dry_run:
                await asyncio.to_thread(
                    _in_session, lambda db: record_attempt(db, series_id, False, 0))
            continue
        if not entries:
            stats["empty"] += 1
            if not dry_run:
                await asyncio.to_thread(
                    _in_session, lambda db: record_attempt(db, series_id, False, 0))
            continue
        if dry_run:
            log.info("would fill %-40s %s -> %d works listed",
                     (name or "")[:40], ao3_id, len(entries))
            stats["series"] += 1
            continue
        def _fill(db, _sid=series_id, _aid=ao3_id, _e=entries, _c=complete):
            r = fill_one(db, _sid, _aid, _e, _c)
            record_attempt(db, _sid, _c, len(_e))
            return r

        try:
            ix, lk = await asyncio.to_thread(_in_session, _fill)
        except Exception as e:
            # The attempt still has to be recorded, in its own session: the
            # failed one is aborted, and without a log row this series sorts
            # straight back to the head of a deterministic queue and re-fails
            # every cycle -- which is the starvation the log exists to stop.
            log.warning("series %s fill failed: %s: %s", ao3_id, type(e).__name__, e)
            try:
                await asyncio.to_thread(
                    _in_session,
                    lambda db: record_attempt(db, series_id, False, len(entries)))
            except Exception:
                log.warning("series %s: could not even record the attempt", ao3_id)
            continue
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
