r"""Keep the searches people actually run out of the cold path.

    docker compose exec backend python cache_warm.py --dry-run
    docker compose exec backend python cache_warm.py

Why
---
Cold search latency tracks the result count, measured on the live site: 64
results in 0.5s, 916 in 1.1s, 2,188 in 1.9s, and the terms that reach the 5,000
candidate ceiling in 3 to 5 seconds. Warm, the same query is 30 to 70ms.

The terms that reach that ceiling are the popular ones -- drarry, harry potter,
wolfstar, dramione -- so the reader who pays 5 seconds is the one searching the
most obvious thing on the site. On a site trying to keep the visitors it gets,
that is the worst possible place for the worst latency.

Caching already amortises it, and cost-scaled TTLs now give an expensive search
hours rather than ten minutes. But SOMEBODY still pays full price at the start
of every window, and it does not have to be a reader.

What it warms
-------------
Only what the traffic data says people search, from visit_events_public -- so
this warms what readers ask for rather than what anybody here guessed they
would. The view excludes our own admin queries, which matters: warming the
Outreach panel's machine-built operator queries would spend the effort on
searches no reader will ever repeat.

It is deliberately not a crawler of every possible query. A term nobody has
searched twice is not worth holding in memory, and the cache has a bounded size
that a warmer could otherwise fill with noise.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text as sql_text  # noqa: E402

from db.session import db_session  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("cache_warm")

# How many distinct people must have searched a term before it is worth warming.
# Searches alone would let one determined visitor -- or one paging session --
# nominate the whole list; "Bts jin and jimin" is 474 searches from 24 people,
# and it is the people that make it popular.
MIN_PEOPLE = int(os.getenv("WARM_MIN_PEOPLE", "3"))
WINDOW_DAYS = int(os.getenv("WARM_WINDOW_DAYS", "60"))
LIMIT = int(os.getenv("WARM_LIMIT", "40"))
# Between requests. These go through the ordinary search path and share a
# database with live readers, so the warmer waits its turn.
GAP = float(os.getenv("WARM_GAP_SECONDS", "1.5"))

POPULAR = """
    SELECT q, count(*) AS searches, count(DISTINCT visitor) AS people
      FROM visit_events_public
     WHERE kind = 'search' AND q IS NOT NULL AND q <> ''
       AND at > now() - make_interval(days => :days)
     GROUP BY q
    HAVING count(DISTINCT visitor) >= :min_people
     ORDER BY count(DISTINCT visitor) DESC, count(*) DESC
     LIMIT :lim
"""


def popular_queries(db) -> list[tuple[str, int, int]]:
    rows = db.execute(sql_text(POPULAR),
                      {"days": WINDOW_DAYS, "min_people": MIN_PEOPLE,
                       "lim": LIMIT}).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def warm(dry_run: bool = False, sleep=time.sleep) -> dict:
    """Run each popular search once so a reader does not have to run it cold.

    Through the ordinary endpoint, on purpose. A warmer that populated the cache
    by some other route would be warming a key the real request never looks
    under -- which is the failure mode that makes a cache warmer worse than
    nothing, because it costs the work and saves none of it.
    """
    from fastapi.testclient import TestClient
    from main import app

    with db_session() as db:
        queries = popular_queries(db)
    if not queries:
        log.info("no query has been searched by %d people yet", MIN_PEOPLE)
        return {"warmed": 0, "slow": 0}

    client = TestClient(app)
    stats = {"warmed": 0, "slow": 0, "failed": 0}
    for q, searches, people in queries:
        if dry_run:
            log.info("    would warm %-34s %d searches, %d people",
                     q[:34], searches, people)
            stats["warmed"] += 1
            continue
        t = time.monotonic()
        try:
            r = client.get("/api/search", params={"q": q, "per_page": 20})
        except Exception as e:
            log.info("    %s: %s", q[:30], type(e).__name__)
            stats["failed"] += 1
            sleep(GAP)
            continue
        ms = (time.monotonic() - t) * 1000
        if r.status_code != 200:
            stats["failed"] += 1
        else:
            stats["warmed"] += 1
            # Worth logging: a term that was already fast did not need warming,
            # and one that took seconds is exactly what a reader would have
            # paid. The ratio says whether this job is earning its requests.
            if ms > 1000:
                stats["slow"] += 1
        sleep(GAP)

    log.info("warmed %d of %d popular searches (%d were cold enough to matter)",
             stats["warmed"], len(queries), stats["slow"])
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    warm(a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
