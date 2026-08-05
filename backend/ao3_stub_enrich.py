r"""
Fill in AO3 rows that were indexed as little more than a title.
===============================================================

4,117,147 of our 13.1M AO3 rows have word_count 0 or NULL, and most of those
also have kudos 0, hits 0 and no summary. They came from bulk sources that
carried an id, a title and an author and nothing else.

That is not only a display problem. It breaks SEARCH BY NAME, and badly:

    73 works on AO3 are called "All the Young Dudes". Five have engagement
    figures in our index; the other 68 are stubs sitting at kudos 0, including
    the one everybody means — MsKingBean89's, which actually has 318,436 kudos,
    20.4M hits and 526,969 words. With nothing to tell them apart, the famous
    one sorted somewhere in an arbitrary run of 68 identical-looking rows.

Ranking cannot fix that, because there is no signal in the data to rank on. The
row has to be filled in.

Which stubs first
-----------------
A stub nobody searches for costs nothing; a stub that COLLIDES with other works
on its title is breaking a search someone is running right now. So collision
count is what you would like to order by — and it is far too expensive to ask
for. GROUP BY lower(title) over 13.1M rows took minutes and had to be killed.
Sorting by title length was no better: with no index on the expression, ORDER BY
still finds and sorts all 4.1M matches before returning ten.

So there is no ORDER BY at all. The title-length bound is a WHERE predicate and
the LIMIT lets the scan stop as soon as it has enough rows — 311ms instead of
minutes. Short titles are a good proxy anyway: one short enough to type in full
("All the Young Dudes", "Home", "Stay") is both the kind that collides and the
kind someone searches by name. Enriched rows stop matching, so successive runs
pick up new ones and the queue drains without being ordered.

Rate limiting
-------------
Goes through ao3_budget, the same limiter every other AO3 path uses, so this
cannot compete with the harvester for AO3's patience. At its steady rate the
full 4.1M is a long job measured in weeks; it is designed to be run repeatedly
and interrupted freely. Every row it completes is a permanent improvement, and
the collision ordering means the first few thousand carry most of the benefit.

    docker compose exec backend python ao3_stub_enrich.py --limit 200 --dry-run
    docker compose exec backend python ao3_stub_enrich.py --limit 5000
"""

import argparse
import asyncio
import logging
import os
import re
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

import httpx
from sqlalchemy import text as sql_text

import ao3_budget
from db.session import db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

UA = "FicAtlas/1.0 (+fanfiction metadata index; contact via site)"

# The stats block on a work page. Every one is optional — a work with no kudos
# simply has no <dd class="kudos">, so a missing field means zero, not an error.
_STAT_RE = {
    "word_count":     re.compile(r'<dd class="words">([\d,]+)</dd>'),
    "kudos":          re.compile(r'<dd class="kudos">([\d,]+)</dd>'),
    "hits":           re.compile(r'<dd class="hits">([\d,]+)</dd>'),
    "bookmarks":      re.compile(r'<dd class="bookmarks">(?:<a[^>]*>)?([\d,]+)'),
    "comments":       re.compile(r'<dd class="comments">([\d,]+)</dd>'),
}
_CHAPTERS_RE = re.compile(r'<dd class="chapters">(\d+)\s*/\s*(\d+|\?)</dd>')

# Rows worth fetching: an AO3 work URL we can actually request, and nothing
# useful recorded against it. Deliberately NOT "word_count = 0" alone — a
# genuinely empty work exists, and refetching it every run forever would be a
# slow leak of AO3's goodwill for no gain.
_CANDIDATES = sql_text("""
    SELECT id, url, length(title) AS tl
    FROM stories
    WHERE site = 'ao3'
      AND url LIKE 'https://archiveofourown.org/works/%'
      AND COALESCE(word_count, 0) = 0
      AND COALESCE(kudos, 0) = 0
      AND COALESCE(hits, 0) = 0
      AND title IS NOT NULL
      AND length(title) BETWEEN 3 AND :maxlen
    LIMIT :lim
""")


def _int(pattern: re.Pattern, html: str) -> int | None:
    m = pattern.search(html)
    return int(m.group(1).replace(",", "")) if m else None


def parse_work(html: str) -> dict:
    out: dict = {}
    for field, pat in _STAT_RE.items():
        v = _int(pat, html)
        if v is not None:
            out[field] = v
    m = _CHAPTERS_RE.search(html)
    if m:
        out["chapter_count"] = int(m.group(1))
        # "12/12" means finished, "12/?" still going. Not written: completion
        # lives in `status`, which the bulk importers already set, and guessing
        # it from a chapter count would overwrite better information.
    m = re.search(r'<blockquote class="userstuff">(.*?)</blockquote>', html, re.S)
    if m:
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out["summary"] = text[:5000]
    return out


async def enrich(limit: int, dry_run: bool, maxlen: int = 60,
                 only_url: str | None = None) -> int:
    with db_session() as db:
        if only_url:
            # One named work. The reason this exists: the queue is a slow
            # background drain, and when a specific search is visibly wrong the
            # fix should not have to wait for it to come round.
            rows = db.execute(sql_text(
                "SELECT id, url, length(title) FROM stories WHERE url = :u"),
                {"u": only_url}).fetchall()
            if not rows:
                log.error(f"no row with url {only_url}")
                return 2
        else:
            rows = db.execute(_CANDIDATES,
                              {"lim": limit, "maxlen": maxlen}).fetchall()
    if not rows:
        log.info("no stubs left to enrich")
        return 0

    log.info(f"{len(rows):,} stubs queued")
    if dry_run:
        for sid, url, _tl in rows[:10]:
            log.info(f"    would fetch {url}")
        return 0

    done = failed = 0
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": UA}) as client:
        for sid, url, _tl in rows:
            await ao3_budget.await_slot()
            try:
                r = await client.get(f"{url}?view_adult=true")
                ao3_budget.note_response(r.status_code,
                                         r.headers.get("retry-after"))
                if r.status_code == 404:
                    # Deleted or made registered-only since we indexed it. Marked
                    # so the next run does not queue it again — worker.py handles
                    # withdrawal separately; this only stops us re-asking.
                    with db_session() as db:
                        db.execute(sql_text(
                            "UPDATE stories SET word_count = -1 WHERE id = :i"),
                            {"i": sid})
                        db.commit()
                    failed += 1
                    continue
                if r.status_code != 200:
                    failed += 1
                    continue
                data = parse_work(r.text)
            except Exception as e:                       # network, parse, decode
                log.debug(f"  {url}: {e}")
                failed += 1
                continue

            if not data:
                failed += 1
                continue

            # Additive: only ever fills a field we have nothing for, so a row
            # already enriched from a listing page is never downgraded by a
            # partial parse here.
            sets, params = [], {"i": sid}
            # Column names, not the scraper's field names — the works scraper
            # calls these bookmark_count/comment_count and the table calls them
            # bookmarks/comments, and building SQL from the wrong set failed
            # every UPDATE after a successful fetch.
            for field in ("word_count", "kudos", "hits", "bookmarks",
                          "comments", "chapter_count"):
                if field in data:
                    sets.append(f"{field} = COALESCE(NULLIF({field}, 0), :{field})")
                    params[field] = data[field]
            if "summary" in data:
                sets.append("summary = COALESCE(NULLIF(summary, ''), :summary)")
                params["summary"] = data["summary"]
            if not sets:
                failed += 1
                continue

            with db_session() as db:
                db.execute(sql_text(
                    f"UPDATE stories SET {', '.join(sets)} WHERE id = :i"), params)
                db.commit()
            done += 1
            if done % 25 == 0:
                log.info(f"  {done:,} enriched, {failed:,} skipped")

    log.info(f"DONE — {done:,} enriched, {failed:,} skipped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill in AO3 rows indexed as bare titles")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--maxlen", type=int, default=60,
                    help="longest title to bother with — short ones collide most")
    ap.add_argument("--url", default=None,
                    help="enrich one named work instead of draining the queue")
    args = ap.parse_args()
    return asyncio.run(enrich(args.limit, args.dry_run, args.maxlen, args.url))


if __name__ == "__main__":
    sys.exit(main())
