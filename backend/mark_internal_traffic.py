"""Flag our own searches in visit_events so the reports stop counting them.

The admin Outreach panel previews every queued Reddit post by running its query
through the public /api/search, deliberately, so the panel shows exactly what a
reader following the link would see. Going forward those are never recorded --
search() marks staff requests and the middleware declines to write the row.
This is for the rows written before that existed.

Matching on reddit_posts.query alone caught 155 of them and missed most: the
panel also runs edited and re-extracted variants, and rows age out of
reddit_posts. So the rule is the panel's SHAPE rather than its exact text, and
it is deliberately a conjunction of three things:

    a visitor-day with NO pageviews at all      - a reader who searches also
                                                  looks at something; 34,773
                                                  visitor-days have pageviews
                                                  and account for 25,199 of the
                                                  searches
    at least ten searches that day              - excludes anyone who simply
                                                  never clicked through
    the query is operator-built                 - fandom:, ship:, char:, tag:,
                                                  xover:

All three, because any one alone is wrong. Measured over the window, the
operator test is what separates us from everybody else: on the days the panel
was in use it matches almost everything (170 of 174, 308 of 314, 465 of 489),
and on the quiet days before it existed it matches nothing at all (0 of 39,
0 of 79, 2 of 93). Those quiet days are somebody else -- a scraper, or someone
driving the API -- and they stay in the reports, where the scrapers panel can
see them.

Flagged, never deleted. The rows are true; somebody did search. They just were
not an audience.
"""
from __future__ import annotations

import logging
import sys

sys.path.insert(0, "/app")
from sqlalchemy import text as sql_text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger(__name__)

MIN_SEARCHES = 10

SQL = """
WITH v AS (
    SELECT visitor, at::date AS day,
           count(*) FILTER (WHERE kind = 'search')  AS searches,
           count(*) FILTER (WHERE kind <> 'search') AS other
      FROM visit_events
     GROUP BY 1, 2
), flagged AS (
    SELECT visitor, day FROM v
     WHERE other = 0 AND searches >= :min_searches
)
UPDATE visit_events e SET internal = TRUE
  FROM flagged f
 WHERE e.visitor = f.visitor
   AND e.at::date = f.day
   AND e.kind = 'search'
   AND NOT e.internal
   AND (e.q LIKE '%xover:%'   OR e.q LIKE '%fandom:"%'
     OR e.q LIKE '%tag:"%'    OR e.q LIKE '%ship:"%'
     OR e.q LIKE '%char:"%')
"""


# OUR OWN BROWSER AUTOMATION, which is neither an audience nor a crawler.
#
# `is_bot` already flags these at write time — tracking._BOT_RE names the
# automation drivers and the client libraries — so they never counted as
# readers. But `bot` means "a crawler" everywhere the reports use it, and the
# traffic panel prints crawler pageviews and searches as their own figure, on
# the argument that "nobody visited" and "nobody except crawlers visited" are
# different facts. Our own test runs are neither: they are this project driving
# its own site, and they inflate the one number that is meant to say whether
# anything out there is indexing us.
#
# Only the two kinds that can be nothing else. `bot_kind = 'bot'` is the broad
# user-agent match and catches real crawlers, so it is deliberately not here.
#
#   headless   Playwright/headless Chrome — this repo's UI checks
#   curl       shell smoke tests
#
# Flagged rather than deleted, like everything else in this file: the rows are
# true, somebody did make those requests. They were just us.
OURS_SQL = """
UPDATE visit_events SET internal = TRUE
 WHERE NOT internal
   AND bot_kind IN ('headless', 'curl')
"""


def run() -> int:
    """Idempotent: only ever sets the flag, and skips rows already carrying it."""
    with db_session() as db:
        n = db.execute(sql_text(SQL), {"min_searches": MIN_SEARCHES}).rowcount or 0
        m = db.execute(sql_text(OURS_SQL)).rowcount or 0
        db.commit()
    log.info("marked %s searches and %s automation rows as internal",
             f"{n:,}", f"{m:,}")
    return n + m


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(f"marked {run():,} rows")
