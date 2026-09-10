"""What ~900 fandoms recommend, from the wiki that has been keeping the lists.

    docker exec ficatlas-backend-1 python tropedia_recs_import.py --dry-run
    docker exec ficatlas-backend-1 python tropedia_recs_import.py
    docker exec ficatlas-backend-1 python tropedia_recs_import.py --limit 50

Why a second source
-------------------
reddit_recs_import.py established that being RECOMMENDED is a measurement this
index otherwise cannot make, and it is right — but it covers exactly one fandom.
958 works, all Harry Potter, out of 20.5M. A reader searching Naruto or Percy
Jackson or The Dresden Files got nothing from it, and "reader-recommended only"
is a filter that quietly means "Harry Potter only".

Tropedia is a Fandom-hosted fork of TVTropes' wiki content, and TVTropes has
maintained per-fandom `Fanfic Recs` pages for two decades. Measured 2026-09-10:
**907 such pages**, averaging 9.4 archive links each, projecting ~8,500 work
references across hundreds of fandoms.

Why Tropedia and not TVTropes itself
------------------------------------
tvtropes.org sits behind a Cloudflare managed challenge — even robots.txt
returns the "Just a moment..." interstitial to a server-side fetch, so there is
no polite way to read it from here and no point pretending otherwise. Tropedia
carries the same body of content under CC-BY-SA and exposes a documented
MediaWiki API, which is the difference between reading a site the way it offers
to be read and working around its front door.

Only the LINKS are taken — which work was recommended, never the prose saying
why. The recommendation text is the wiki's copyrighted contribution; that a
given fanfic appears on a given list is a fact about the world.

Shape
-----
Mirrors reddit_recs_import.py, because the search API already knows that shape:

    community_recs    the generic marker — some community recommends this
    tropedia_recs     this source specifically

`community_recs` is written by BOTH importers and is what the UI filters on, so
"reader-recommended" stops meaning "Harry Potter". The reddit import keeps its
own `reddit_recs` and `reddit_refs:N`; the numeric count is real there and has
no equivalent here, because a wiki rec list is a yes, not a tally.

Matching is by ARCHIVE ID parsed from the URL, never by title — this index holds
five works called "Manacled", and a wrong match attaches somebody else's
reputation to the wrong story.

Deliberately additive: only works already indexed are touched, only these two
tags are written, and works on a list but not in the index are counted and left
alone. They are a crawl target, not a row to invent.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("tropedia_recs")

API = "https://tropedia.fandom.com/api.php"
CATEGORY = "Category:Fanfic Recs"

# Named, with a contact URL, because that is what a crawler owes a site whose
# API it is using at volume. ~900 requests per full run.
UA = {"User-Agent": "FicAtlas/1.0 (+https://ficatlas.com; cross-archive fanfiction index)"}

# The generic marker, written here AND backfilled onto the reddit import. This
# is what "reader-recommended only" filters on.
MARKER = "community_recs"
# The source, kept alongside it so a bad source can be identified and removed
# without touching the other one's rows.
SOURCE_MARKER = "tropedia_recs"

# Politeness. Fandom serves this API for free and a full run is ~900 calls;
# at this spacing that is roughly four minutes and no burst worth noticing.
DELAY_S = float(os.getenv("TROPEDIA_DELAY_S", "0.25"))

_FFNET = re.compile(r"fanfiction\.net/s/(\d+)")
_AO3 = re.compile(r"archiveofourown\.org/works/(\d+)")


def _api(**params) -> dict:
    params.setdefault("format", "json")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def rec_pages() -> list[str]:
    """Every page in Category:Fanfic Recs, following continuation."""
    titles: list[str] = []
    cont: str | None = None
    while True:
        params = dict(action="query", list="categorymembers", cmtitle=CATEGORY,
                      cmlimit=500, cmnamespace=0)
        if cont:
            params["cmcontinue"] = cont
        d = _api(**params)
        titles += [m["title"] for m in d.get("query", {}).get("categorymembers", [])]
        cont = d.get("continue", {}).get("cmcontinue")
        if not cont:
            return titles
        time.sleep(DELAY_S)


def works_on(title: str) -> set[tuple[str, str]]:
    """(site, site_id) for every archive work linked from one rec page.

    `prop=externallinks` rather than parsing wikitext: the wiki's rec pages use
    several link templates and markup styles accumulated over twenty years, and
    the parser already knows how to resolve all of them into plain URLs.

    A set, because a page routinely links the same work from several chapters
    ("/s/5430338/1/", "/s/5430338/12/") and it is one recommendation.
    """
    d = _api(action="parse", page=title, prop="externallinks")
    links = d.get("parse", {}).get("externallinks", [])
    out: set[tuple[str, str]] = set()
    for link in links:
        m = _FFNET.search(link)
        if m:
            out.add(("ffnet", m.group(1)))
            continue
        a = _AO3.search(link)
        if a:
            out.add(("ao3", a.group(1)))
    return out


# One statement per work, and the same array surgery reddit_recs_import uses:
# strip any previous copy of each marker, then append. A re-run therefore
# corrects rather than accumulating a duplicate tag per import.
_UPSERT = text("""
    UPDATE stories
       SET tags = ARRAY(
             SELECT x FROM unnest(coalesce(tags, '{}')) x
              WHERE x <> :marker AND x <> :source)
           || ARRAY[:marker, :source]::text[]
     WHERE site::text = :site AND site_id = :site_id
       AND delisted_at IS NULL
""")

# Everything the reddit import already found is a community recommendation too.
# Without this, switching the UI filter to `community_recs` would silently drop
# the 958 works that were the entire feature until today.
_BACKFILL_REDDIT = text("""
    UPDATE stories
       SET tags = array_append(tags, :marker)
     WHERE tags @> ARRAY['reddit_recs']::text[]
       AND NOT (tags @> ARRAY[:marker]::text[])
       AND delisted_at IS NULL
""")


# How many pages to read before writing what they found. Small enough that an
# interrupted run keeps nearly everything, large enough not to open a
# transaction per page.
BATCH_PAGES = 25


def run(dry_run: bool = False, limit: int | None = None) -> dict:
    """Read every rec page and tag what it names, committing as it goes.

    Committing in BATCHES rather than once at the end, and the difference is not
    theoretical: the first full run read 50 of 907 pages, was interrupted, and
    wrote nothing at all — four minutes of somebody else's API quota spent for
    no rows, because the single commit came after the loop. A network read of
    ~900 pages will be interrupted sooner or later, and it should cost the
    pages not yet read rather than all of them.

    That also makes the job resumable by simply re-running it: `_UPSERT` strips
    each marker before appending it, so a work tagged twice ends up tagged once.
    """
    titles = rec_pages()
    if limit:
        titles = titles[:limit]
    log.info("tropedia: %d rec pages to read", len(titles))

    stats = {"pages": 0, "listed": 0, "matched": 0, "missing": 0, "backfilled": 0}
    seen: set[tuple[str, str]] = set()
    pending: set[tuple[str, str]] = set()

    def flush() -> None:
        """Write what the last batch of pages named, and forget it."""
        if not pending or dry_run:
            pending.clear()
            return
        with db_session() as db:
            for site, site_id in sorted(pending):
                res = db.execute(_UPSERT, {"marker": MARKER, "source": SOURCE_MARKER,
                                           "site": site, "site_id": site_id})
                if res.rowcount:
                    stats["matched"] += res.rowcount
                else:
                    stats["missing"] += 1
            db.commit()
        pending.clear()

    for i, title in enumerate(titles, 1):
        try:
            found = works_on(title)
        except Exception as e:
            # One unreadable page must not end a 900-page run.
            log.warning("tropedia: %s failed (%s)", title, type(e).__name__)
            continue
        stats["pages"] += 1
        # Only what has not already been written, so a work recommended on five
        # pages costs one UPDATE rather than five.
        fresh = found - seen
        seen |= found
        pending |= fresh
        if i % BATCH_PAGES == 0:
            flush()
            log.info("tropedia: %d/%d pages, %d works listed, %d tagged",
                     i, len(titles), len(seen), stats["matched"])
        time.sleep(DELAY_S)
    flush()
    stats["listed"] = len(seen)

    if dry_run:
        log.info("tropedia: would tag %d works from %d pages (dry run)",
                 len(seen), stats["pages"])
        return stats

    # Last, and only on a run that reached the end: everything the reddit import
    # found is a community recommendation too, and without this, pointing the UI
    # at `community_recs` would silently drop the 958 works that were the whole
    # feature until today.
    with db_session() as db:
        stats["backfilled"] = db.execute(
            _BACKFILL_REDDIT, {"marker": MARKER}).rowcount or 0
        db.commit()

    log.info("tropedia: %d pages, %d listed, %d matched, %d not indexed, "
             "%d reddit rows backfilled",
             stats["pages"], stats["listed"], stats["matched"],
             stats["missing"], stats["backfilled"])
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="read the wiki and report, write nothing")
    ap.add_argument("--limit", type=int, default=None,
                    help="only read the first N rec pages")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(dry_run=args.dry_run, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
