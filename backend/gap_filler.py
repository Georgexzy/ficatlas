r"""
Route each story with missing metadata to the source most likely to have it.
============================================================================

The index is assembled from sources that are each wide in different places and
empty in others, so no single one closes the gaps:

    AO3 metadata dump   13.0M rows   NO summaries at all, 30% no dates,
                                     titles truncated mid-phrase
    HF FFN dump          6.6M rows   NO dates, NO characters/ships,
                                     NO engagement counts
    FicAlley dump         30k rows   near complete
    AO3 tag LISTINGS       live      20 works per request — the bulk route
    AO3 work pages         live      1 work per request; only for defects a
                                     listing cannot show, e.g. a title the dump
                                     truncated
    Wayback (FFN)          live      ~69% hit rate, 2 requests each
    FicHub /api/v0/meta    live      FFN + AO3, 1 request, no downloads

Before this, each backfill owned its own queue and its own idea of what was
missing, so a row could be picked up by one loop for a field another loop had
already filled, and nothing ever asked "what does THIS row still lack, and who
is most likely to have it".

This picks per row instead:

  * AO3     -> a tag LISTING, not the work page. A listing carries the same
               fields for twenty works at once (measured: 19/20 with a summary,
               20/20 with word count and date), so the same coverage costs
               650k requests instead of 13M — and 20x less load on AO3 for
               identical data. See ao3_listing_harvest.py.

               Work pages are still fetched, but only for what a listing cannot
               fix: a title the dump truncated is wrong in the listing too,
               because the listing is generated from the same field.
  * FF.net  -> Wayback first (built for bulk), FicHub only where Wayback has
               no capture. FicHub is one volunteer's server.
  * others  -> FicHub, which resolves anything it can reach.

and it ranks the queue by what a reader will actually see, so effort lands on
works people open rather than on the long tail.

A correction worth recording, since it was believed for a while and shaped
several decisions: the 13M-row summary gap was called structurally unfixable
on the arithmetic of one-work-per-request. That arithmetic was right and the
premise was wrong. Nothing about the volume changed; the request shape did.

Gaps are scored, not merely counted: a missing summary matters more than a
missing language, because a result with no description is the one that looks
broken in a list.
"""

import logging

from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)

# What each field is worth fixing, roughly by how visible it is to a reader.
# A summary is what makes a search result judgeable at a glance; engagement
# only affects ranking; language only matters to a filter.
FIELD_WEIGHTS: dict[str, int] = {
    "summary": 5,
    "published_at": 3,
    "word_count": 3,
    "characters": 2,
    "relationships": 2,
    "updated_at": 2,
    "kudos": 1,
    "language": 1,
}

# Rows whose gaps score below this are not worth a request of anyone's time.
MIN_SCORE = 3

# Ordered by (worth fixing) x (likely to be seen), then least-recently checked
# so the queue always advances and nothing can camp at the head.
CANDIDATES_SQL = r"""
    SELECT id, site, site_id, url,
           (CASE WHEN summary IS NULL OR summary = '' THEN :w_summary ELSE 0 END
          + CASE WHEN published_at IS NULL           THEN :w_published ELSE 0 END
          + CASE WHEN word_count IS NULL OR word_count = 0 THEN :w_words ELSE 0 END
          + CASE WHEN cardinality(characters) = 0    THEN :w_chars ELSE 0 END
          + CASE WHEN cardinality(relationships) = 0 THEN :w_ships ELSE 0 END
          + CASE WHEN updated_at IS NULL             THEN :w_updated ELSE 0 END
          + CASE WHEN COALESCE(kudos,0) = 0 AND COALESCE(hits,0) = 0 THEN :w_kudos ELSE 0 END
          + CASE WHEN language IS NULL OR language = '' THEN :w_lang ELSE 0 END
           ) AS gap_score
    FROM stories
    WHERE site = :site
      AND site_id ~ '^[0-9]+$'
    ORDER BY gap_score DESC,
             (COALESCE(kudos, 0) + COALESCE(hits, 0)) DESC,
             COALESCE(word_count, 0) DESC,
             date_trunc('day', crawled_at AT TIME ZONE 'UTC') ASC NULLS FIRST
    LIMIT :lim
"""


def _weights() -> dict:
    return {
        "w_summary":   FIELD_WEIGHTS["summary"],
        "w_published": FIELD_WEIGHTS["published_at"],
        "w_words":     FIELD_WEIGHTS["word_count"],
        "w_chars":     FIELD_WEIGHTS["characters"],
        "w_ships":     FIELD_WEIGHTS["relationships"],
        "w_updated":   FIELD_WEIGHTS["updated_at"],
        "w_kudos":     FIELD_WEIGHTS["kudos"],
        "w_lang":      FIELD_WEIGHTS["language"],
    }


def find_gaps(db, site: str, limit: int = 100) -> list[dict]:
    """Rows of `site` most worth filling, worst-and-most-visible first."""
    rows = db.execute(sql_text(CANDIDATES_SQL),
                      {"site": site, "lim": limit, **_weights()}).fetchall()
    return [
        {"id": r[0], "site": r[1], "site_id": r[2], "url": r[3], "gap_score": r[4]}
        for r in rows if (r[4] or 0) >= MIN_SCORE
    ]


def describe(db) -> dict[str, dict]:
    """Per-site gap summary, for logging and the index-status widget."""
    out: dict[str, dict] = {}
    rows = db.execute(sql_text("""
        SELECT site,
               count(*) AS total,
               count(*) FILTER (WHERE summary IS NULL OR summary = '')      AS no_summary,
               count(*) FILTER (WHERE published_at IS NULL)                 AS no_published,
               count(*) FILTER (WHERE cardinality(characters) = 0)          AS no_characters,
               count(*) FILTER (WHERE COALESCE(kudos,0)=0 AND COALESCE(hits,0)=0) AS no_engagement
        FROM stories GROUP BY site
    """)).fetchall()
    for r in rows:
        out[str(r[0])] = {
            "total": r[1], "no_summary": r[2], "no_published": r[3],
            "no_characters": r[4], "no_engagement": r[5],
        }
    return out
