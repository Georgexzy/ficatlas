r"""
Fill AO3 metadata from LISTING pages, 20 works per request.
===========================================================

The work-page harvest in ao3_title_repair.py fetches one work per request. That
was the wrong shape and it is why "13M summaries" looked impossible: at ~0.5
req/s a work at a time is 13 million requests.

A tag-works listing carries the same fields for TWENTY works in one response.
Measured on a live page: 19/20 with a summary, 20/20 with word count and date,
18/20 with characters. So the same coverage costs 650,000 requests instead of
13,000,000 — and that is a 20x REDUCTION in load on AO3 for identical data,
which makes this the polite option as well as the fast one.

    work pages    13,000,000 requests   ~300 days at 0.5 req/s
    listings         650,000 requests    ~15 days at 0.5 req/s

Deep pagination is not capped — page 5000 of a large fandom still returns 20
works, verified live.

On scale and manners
--------------------
AO3's position (admin_posts/25888) is that they rate-limit and watch for
abusive collection, make no exceptions for dataset-building, but have "no
policy against responsible data collection". This walks fandoms largest-first
so the works people actually search for are filled in early, and it can be
stopped at any point with most of the benefit already banked — it does not
need to run to completion to be worth running. Rate limiting is shared with
the rest of the AO3 work, not additional to it.

Coverage comes from walking canonical fandoms, which
`ao3_canonical_fandoms.py` already synced (73,732 of them, with AO3's own work
counts). Biggest fandoms first is also the best request-per-work ratio: a
fandom with 500k works yields 20 per page all the way down, while the tail is
full of fandoms with three works and one mostly-empty page each.
"""

import logging
import os

from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)

# Fandoms below this are skipped: a page fetch that returns three works costs
# the same as one returning twenty, so the tail is where the request budget
# goes to die. 73,732 canonical fandoms, but the large ones hold most works.
MIN_FANDOM_WORKS = int(os.getenv("LISTING_MIN_FANDOM", "200"))

# How many pages of one fandom to take before moving on. Small, so that a
# 25,000-page fandom cannot monopolise the queue while everything else waits.
PAGES_PER_VISIT = int(os.getenv("LISTING_PAGES_PER_VISIT", "5"))

# Two queues, alternated, because "fill in what we hold" and "find what we do
# not" are different jobs and ranking by one starves the other.
#
# DISCOVER walks AO3's biggest fandoms. Those turn out to be ones the bulk dump
# barely covered — RPF, K-pop, Marvel — so a pass there is nearly all new works
# and closes the post-2024 gap the dump cannot.
#
# BACKFILL walks the fandoms WE hold the most of, which is where the missing
# summaries actually are: 100% of AO3 rows arrived without one, so "most of our
# works" is also "most of our gaps". Restricted to names that exist as canonical
# AO3 tags, since that is what the tag URL needs — our own facet list contains
# FF.net spellings like "Harry Potter" that AO3 has no tag page for.
DISCOVER_SQL = sql_text("""
    SELECT value, count
    FROM facets
    WHERE kind = 'fandom_ao3' AND count >= :min_works
    ORDER BY count DESC
    LIMIT :lim
""")

BACKFILL_SQL = sql_text("""
    SELECT f.value, f.count
    FROM facets f
    JOIN facets a ON a.kind = 'fandom_ao3' AND a.value = f.value
    WHERE f.kind = 'fandom' AND f.count >= :min_works
    ORDER BY f.count DESC
    LIMIT :lim
""")


def next_fandoms(db, limit: int = 50, mode: str = "discover") -> list[tuple[str, int]]:
    """Canonical fandoms worth walking. `mode` picks which queue."""
    sql = BACKFILL_SQL if mode == "backfill" else DISCOVER_SQL
    rows = db.execute(sql, {"min_works": MIN_FANDOM_WORKS, "lim": limit}).fetchall()
    return [(r[0], r[1]) for r in rows]


def cursor_key(fandom: str, mode: str = "discover") -> str:
    # Fandom names contain everything including slashes and quotes; hash to keep
    # the settings key short and safe rather than storing the name itself.
    import hashlib
    # Mode is part of the key: the two queues walk the same fandom from
    # different ends of the same list and must not share a page cursor.
    return f"listing_page:{mode}:" + hashlib.sha1(fandom.encode("utf-8")).hexdigest()[:16]


def get_cursor(db, fandom: str, mode: str = "discover") -> int:
    from api.settings import get_setting
    raw = get_setting(db, cursor_key(fandom, mode))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def set_cursor(db, fandom: str, page: int, mode: str = "discover") -> None:
    from api.settings import put_setting
    put_setting(db, cursor_key(fandom, mode), str(page))
