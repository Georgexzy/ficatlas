"""Crawlable entry points into the index, one per fandom.

Why this exists
---------------
Story pages carry real metadata now (see frontend/app/story/[id]/page.tsx), so
they are worth indexing. Nothing could reach them. No page on the site emits a
static href to /story/…: the only route in is the search box, and search URLs
are `/?q=…`, which robots.txt blocks deliberately because the combination space
of q x fandoms x tags x ratings x sort x page is infinite and is the single most
likely way this site falls over. So the pages were indexable in principle and
unreachable in practice.

The obvious fix is a sitemap of every story. That is 19.9M URLs — ~400 files at
Google's 50k limit — and it invites a crawl of 19.9M pages against a home
connection, which is the same trap robots.txt exists to avoid, entered through
a different door.

A hub per fandom is bounded instead. Each hub is a real page a human landing
cold would want, it links to a capped set of works, and the number of hubs is
chosen rather than implied by the size of the index.

Why the top works are precomputed
---------------------------------
Ranking on demand means `WHERE fandoms && ARRAY[...] ORDER BY kudos DESC LIMIT n`
over, for Harry Potter, 686,558 rows: a GIN lookup and then a large sort, on
every crawl of every hub. Precomputing turns each hub into one primary-key
lookup plus a fetch of ~60 ids. The build is slow and offline; the serving path
is not, which is the half a crawler touches.

Collapsing variants
-------------------
"Harry Potter" and "Harry Potter - J. K. Rowling" are separate facet rows for
the same fandom — 686,558 and 381,225 works. Two hubs for one fandom would be
duplicate content pointing at overlapping work. fandom_base() already exists in
api/search.py for exactly this reason and is reused here rather than reinvented,
so the hubs collapse the same way search does.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.search import fandom_base

log = logging.getLogger(__name__)

# How many works a hub links to. Enough to be a genuinely useful page and to
# give a crawler a real sample of the index; small enough that hubs x works
# stays a number we chose. At the default threshold this is ~323k reachable
# story pages, against 19.9M if every story were listed.
WORKS_PER_HUB = 60

# Minimum works for a fandom to get a hub. 92,018 fandom tags exist; most are
# one-off freeform noise. The thresholds measured on this index:
#
#     >= 1000 works   1,960 fandoms
#     >=  200 works   5,389 fandoms
#     >=   50 works  12,204 fandoms
#
# 200 is the knee: it covers anything with a real readership while keeping the
# hub count in the thousands rather than the tens of thousands.
MIN_WORKS_FOR_HUB = 200

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """A stable, URL-safe id for a fandom name.

    Accents are folded rather than dropped so "Pokémon" and "Pokemon" land on
    the same slug instead of on "pokmon".
    """
    folded = unicodedata.normalize("NFKD", name or "")
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")


def _collapse(rows: Iterable[tuple[str, int]]) -> dict[str, dict]:
    """Group facet rows into one entry per fandom, keyed by slug.

    `count` is summed across variants only as an ordering hint for which hubs
    matter most. It overstates the true total, because a work tagged with both
    "Harry Potter" and "Harry Potter - J. K. Rowling" is counted twice. The
    figure shown to a reader is the exact one recomputed in build_hubs(); this
    is not that number and is never displayed.
    """
    hubs: dict[str, dict] = {}
    for value, count in rows:
        base = fandom_base(value)
        slug = slugify(base)
        if not slug:
            continue
        hub = hubs.setdefault(slug, {"name": base, "variants": [], "approx": 0})
        hub["variants"].append(value)
        hub["approx"] += count
        # Prefer the shortest variant as the display name: "Harry Potter" reads
        # better than "Harry Potter - J. K. Rowling" as a page heading.
        if len(base) < len(hub["name"]):
            hub["name"] = base
    return hubs


def build_hubs(db: Session, min_works: int = MIN_WORKS_FOR_HUB,
               per_hub: int = WORKS_PER_HUB, limit: int | None = None) -> int:
    """Rebuild fandom_hubs from the facets table. Returns the number written.

    Offline by design — this is minutes of work, not a request path.
    """
    db.execute(text("SET statement_timeout = 0"))

    rows = db.execute(text(
        "SELECT value, count FROM facets WHERE kind = 'fandom' AND count >= :m"
    ), {"m": min_works}).fetchall()
    hubs = _collapse((r[0], r[1]) for r in rows)

    ordered = sorted(hubs.items(), key=lambda kv: -kv[1]["approx"])
    if limit:
        ordered = ordered[:limit]

    written = 0
    for slug, hub in ordered:
        variants = hub["variants"]
        try:
            # Ranked by kudos: the works most likely to be worth a stranger's
            # first click, which is also what makes the hub worth indexing.
            # NULLS LAST so unrated works do not lead the page.
            top = db.execute(text("""
                SELECT id FROM stories
                 WHERE fandoms && :variants
                   AND delisted_at IS NULL
                   AND source_restricted_at IS NULL
                 ORDER BY kudos DESC NULLS LAST, word_count DESC NULLS LAST
                 LIMIT :n
            """), {"variants": variants, "n": per_hub}).scalars().all()

            exact = db.execute(text("""
                SELECT count(*) FROM stories
                 WHERE fandoms && :variants AND delisted_at IS NULL
            """), {"variants": variants}).scalar_one()
        except Exception:
            # One bad fandom must not abandon the rest of a long rebuild.
            log.exception("hub build failed for %s", slug)
            db.rollback()
            continue

        if not top:
            continue

        db.execute(text("""
            INSERT INTO fandom_hubs (slug, name, variants, work_count, top_ids, built_at)
            VALUES (:slug, :name, :variants, :wc, :top, now())
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name, variants = EXCLUDED.variants,
                work_count = EXCLUDED.work_count, top_ids = EXCLUDED.top_ids,
                built_at = EXCLUDED.built_at
        """), {"slug": slug, "name": hub["name"], "variants": variants,
               "wc": exact, "top": [str(i) for i in top]})
        written += 1
        if written % 200 == 0:
            db.commit()
            log.info("built %d hubs", written)

    db.commit()

    # A fandom that fell below the threshold, or whose works were all delisted,
    # leaves a hub behind that would 200 with stale contents and keep being
    # crawled. Drop anything this run did not touch.
    stale = db.execute(text(
        "DELETE FROM fandom_hubs WHERE built_at < now() - interval '1 hour'"
    )).rowcount
    db.commit()
    if stale:
        log.info("removed %d stale hubs", stale)

    return written


if __name__ == "__main__":
    import argparse
    from db.session import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Rebuild fandom hub pages.")
    ap.add_argument("--min-works", type=int, default=MIN_WORKS_FOR_HUB)
    ap.add_argument("--per-hub", type=int, default=WORKS_PER_HUB)
    ap.add_argument("--limit", type=int, default=None,
                    help="Only build the N largest fandoms (for a trial run).")
    args = ap.parse_args()

    with SessionLocal() as s:
        n = build_hubs(s, args.min_works, args.per_hub, args.limit)
    log.info("wrote %d hubs", n)
