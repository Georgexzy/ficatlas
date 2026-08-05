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

Which stubs, and why this route at all
--------------------------------------
There is a much cheaper way to fix an AO3 row, and it is already running:
ao3_listing_harvest walks fandom tag pages and gets the same fields for TWENTY
works per request. gap_filler is explicit that work pages are only justified for
what a listing cannot supply. Filling stubs one page at a time would be twenty
times the load on AO3 for identical data — so this deliberately does not compete
with it, and takes only the rows the listing route can never reach.

Measured: about 30% of our AO3 rows — roughly 3.9M — have NO FANDOM AT ALL, and
essentially every one of those is also a stub. A harvest that walks fandom tag
pages cannot see them by construction. Nothing else will ever fix them.

They are also the worst rows in the index to be a reader looking for: no fandom
to filter by, no summary to judge by, no kudos to rank by. Fetching the work
page fixes all of that at once AND gives the row its fandoms, after which the
listing harvest can maintain it like any other.

Within that set, ordering is by gap score — the same weighting gap_filler uses,
where a missing summary counts for more than a missing language because a result
with no description is the one that looks broken in a list — and then by
engagement, so effort lands on works people actually open.

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
import html as _html
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
    SELECT id, url,
           (CASE WHEN nullif(summary,'') IS NULL          THEN 5 ELSE 0 END
          + CASE WHEN published_at IS NULL                THEN 3 ELSE 0 END
          + CASE WHEN COALESCE(word_count,0) = 0          THEN 3 ELSE 0 END
          + CASE WHEN cardinality(characters) = 0         THEN 2 ELSE 0 END
          + CASE WHEN cardinality(relationships) = 0      THEN 2 ELSE 0 END
          + CASE WHEN COALESCE(kudos,0) = 0
                  AND COALESCE(hits,0) = 0                THEN 1 ELSE 0 END
           ) AS gap_score
    FROM stories
    WHERE site = 'ao3'
      AND url LIKE 'https://archiveofourown.org/works/%'
      -- The whole point: rows the fandom-tag harvest cannot reach.
      AND (fandoms IS NULL OR cardinality(fandoms) = 0)
      AND COALESCE(word_count, 0) = 0
      AND COALESCE(kudos, 0) = 0
      AND COALESCE(hits, 0) = 0
      AND title IS NOT NULL
    ORDER BY gap_score DESC, COALESCE(hits,0) DESC, id
    LIMIT :lim
""")


def _unescape(v: str) -> str:
    """AO3 tag text is HTML-escaped, and ampersands are common in fandom names
    ("Steven Universe &amp; Related Fandoms"). Storing the escaped form would
    make the tag fail to match anything already in the index."""
    return _html.unescape(re.sub(r"<[^>]+>", "", v)).strip()


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
    # Fandoms, characters and ships. These are the point of the whole exercise
    # for a fandom-less row: once it HAS a fandom, ao3_listing_harvest can see it
    # on a tag page and maintain it twenty-at-a-time from then on. Without this
    # the row would be filled once and then go stale forever, which is the
    # situation it is already in.
    for field, css in (("fandoms", "fandom"), ("characters", "character"),
                       ("relationships", "relationship")):
        block = re.search(rf'<dd class="{css} tags">(.*?)</dd>', html, re.S)
        if not block:
            continue
        vals = [re.sub(r"\s+", " ", v).strip() for v in
                re.findall(r'<a[^>]*class="tag"[^>]*>(.*?)</a>', block.group(1), re.S)]
        vals = [_unescape(v) for v in vals if v.strip()]
        if vals:
            out[field] = vals[:60]

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
                "SELECT id, url, 0 AS gap_score FROM stories WHERE url = :u"),
                {"u": only_url}).fetchall()
            if not rows:
                log.error(f"no row with url {only_url}")
                return 2
        else:
            rows = db.execute(_CANDIDATES, {"lim": limit}).fetchall()
    if not rows:
        log.info("no stubs left to enrich")
        return 0

    log.info(f"{len(rows):,} queued" if only_url else
             f"{len(rows):,} fandom-less stubs queued (top gap score {rows[0][2]})")
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
            # Arrays are filled only where ours is empty, same additive rule —
            # a listing harvest may already have given this row better tags.
            for field in ("fandoms", "characters", "relationships"):
                if data.get(field):
                    sets.append(f"{field} = CASE WHEN cardinality({field}) = 0 "
                                f"THEN CAST(:{field} AS text[]) ELSE {field} END")
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
