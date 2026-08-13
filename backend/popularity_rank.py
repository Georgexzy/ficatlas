"""Cross-archive popularity, on one scale.

    docker exec ficatlas-backend-1 python popularity_rank.py --dry-run
    docker exec ficatlas-backend-1 python popularity_rank.py

The problem
-----------
"Most kudos" is not a question the index can answer honestly, because the three
archives do not count the same things and do not count them on the same scale.
Measured on this index:

                 avg kudos/favs   avg comments   avg bookmarks
    AO3                     190             57              44
    FanFiction.net        1,676          1,113           1,122
    FictionAlley            969            565             461

A FanFiction.net work reads roughly nine times more popular than an AO3 work
that is loved exactly as much. Sorting the merged index by a raw column
therefore sorts mostly by which site a work came from, which is what "sort by
hits puts all the AO3 first" actually was.

Why percentiles rather than a multiplier
----------------------------------------
The obvious fix is a per-site scale factor. It is the wrong one here: those
averages are not a fixed property of the archives, they are a property of what
we have collected so far. The FF.net figures above are inflated because the
harvester walks most-favourited first, so they will fall as coverage grows, and
any constant calibrated today is wrong next month.

A percentile is self-correcting. "Top 1% of AO3 by kudos" and "top 1% of FF.net
by favs" mean the same thing regardless of scale, of how many rows we hold, or
of how the two communities differ in how freely they click a button. It costs
absolute magnitude — the gap between #1 and #2 is flattened — which is the right
trade for a ranking whose only job is ordering.

Zero is not a low score, it is no score
---------------------------------------
The bulk imports wrote 0 into every engagement column. 97% of AO3 rows and
almost all FF.net rows say 0 for reasons that have nothing to do with how well
they were received. So percentiles are computed over the works that HAVE a
figure, and a work with none gets no popularity at all rather than a bad one —
NULL, which sorts out of the way instead of pretending to be unpopular.

Weights
-------
Ordered by how much intent each action costs the reader:

    bookmarks / follows  0.35   keeping it, or asking to be told it changed
    kudos / favs         0.30   approval, one click
    comments / reviews   0.20   the most effort, but scales with chapter count,
                                so a long serial out-earns a better one-shot
    hits                 0.15   weakest: a click, not a verdict, and inflated by
                                re-reads. Absent from FF.net entirely.

Only the metrics a work actually has are used, and the weights are renormalised
over those — otherwise a work with three signals is punished against one with
four purely for what nobody recorded.

Confidence
----------
A work rated on one metric is a weaker claim than one rated on four, so the
blend is shrunk toward the middle by the share of weight actually observed. This
is the Bayesian-average idea in its simplest useful form: with little evidence,
stay near the prior; with lots, trust the data.

Age
---
Engagement accumulates, so an old work out-scores a better new one given time.
The fix is not to penalise age — a work loved for twenty years IS popular — but
to let the two views argue: 75% absolute standing, 25% standing per unit of time
alive. sqrt(age) rather than age, because a work does not earn readers at a
constant rate for a decade; most of the traffic arrives early.

Dates are missing for 65% of scored AO3 rows and some are epoch artefacts
(1950-01-01, 1969-12-31), so the rate term applies only where a date is credible
and the absolute term carries the rest.
"""
import argparse
import logging

from sqlalchemy import text

from db.session import db_session

log = logging.getLogger("popularity")

W_BOOKMARKS = 0.35
W_KUDOS     = 0.30
W_COMMENTS  = 0.20
W_HITS      = 0.15

# Below this, the shrink toward 0.5 dominates. Named rather than inlined because
# it is the one number that decides how much a single-signal work is trusted.
PRIOR = 0.5

# Anything before this is a placeholder, not a publication date. AO3 did not
# exist in 1950 and FF.net did not exist in 1969; both values appear in the data
# as import artefacts and would otherwise be scored as the oldest, most
# established works in the index.
EPOCH_FLOOR = "1998-01-01"

SQL = f"""
WITH scored AS (
    SELECT id, site, published_at,
           NULLIF(kudos, 0)     AS kudos,
           NULLIF(bookmarks, 0) AS bookmarks,
           NULLIF(comments, 0)  AS comments,
           NULLIF(hits, 0)      AS hits,
           -- Age in days, only where the date is credible. Floored at 30 so a
           -- work published this week is not divided by ~0 and launched to the
           -- top of the index on a handful of kudos.
           CASE WHEN published_at IS NOT NULL
                 AND published_at >= DATE '{EPOCH_FLOOR}'
                 AND published_at <= now()
                THEN GREATEST(EXTRACT(EPOCH FROM (now() - published_at)) / 86400.0, 30.0)
           END AS age_days
      FROM stories
     WHERE delisted_at IS NULL
       AND (kudos > 0 OR bookmarks > 0 OR comments > 0 OR hits > 0)
),
-- PARTITION BY site is the whole parity mechanism: every percentile below is a
-- standing among that work's OWN archive, so the three become comparable
-- without ever being compared directly.
--
-- One CTE per metric, each filtered to the rows that HAVE that metric, rather
-- than one window over everything. This is not tidiness. percent_rank() sorts
-- NULLs last, so a single window scored every work missing a figure as if it
-- held the highest one in the archive: the first run put FF.net works with
-- kudos=0 and comments=0 at the very top of the index, above Harry Potter and
-- the Methods of Rationality, with scores over 1.0. A missing metric has to be
-- absent from its percentile population, not sorted within it.
p_kudos AS (
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY kudos) AS v
      FROM scored WHERE kudos IS NOT NULL
),
p_bookmarks AS (
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY bookmarks) AS v
      FROM scored WHERE bookmarks IS NOT NULL
),
p_comments AS (
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY comments) AS v
      FROM scored WHERE comments IS NOT NULL
),
p_hits AS (
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY hits) AS v
      FROM scored WHERE hits IS NOT NULL
),
-- The same standing, per unit of time alive, over the works that have both the
-- metric and a credible date.
r_kudos AS (
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY kudos / sqrt(age_days)) AS v
      FROM scored WHERE kudos IS NOT NULL AND age_days IS NOT NULL
),
r_bookmarks AS (
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY bookmarks / sqrt(age_days)) AS v
      FROM scored WHERE bookmarks IS NOT NULL AND age_days IS NOT NULL
),
pct AS (
    SELECT s.id,
           pk.v AS p_kudos, pb.v AS p_bookmarks, pc.v AS p_comments, ph.v AS p_hits,
           rk.v AS r_kudos, rb.v AS r_bookmarks,
           s.kudos, s.bookmarks, s.comments, s.hits, s.age_days
      FROM scored s
      LEFT JOIN p_kudos     pk ON pk.id = s.id
      LEFT JOIN p_bookmarks pb ON pb.id = s.id
      LEFT JOIN p_comments  pc ON pc.id = s.id
      LEFT JOIN p_hits      ph ON ph.id = s.id
      LEFT JOIN r_kudos     rk ON rk.id = s.id
      LEFT JOIN r_bookmarks rb ON rb.id = s.id
),
blended AS (
    SELECT id,
           -- Weight actually observed, so the renormalisation below divides by
           -- what was measurable rather than by the full 1.0.
           (CASE WHEN p_kudos     IS NOT NULL THEN {W_KUDOS}     ELSE 0 END
          + CASE WHEN p_bookmarks IS NOT NULL THEN {W_BOOKMARKS} ELSE 0 END
          + CASE WHEN p_comments  IS NOT NULL THEN {W_COMMENTS}  ELSE 0 END
          + CASE WHEN p_hits      IS NOT NULL THEN {W_HITS}      ELSE 0 END) AS w_have,
           (COALESCE(p_kudos     * {W_KUDOS},     0)
          + COALESCE(p_bookmarks * {W_BOOKMARKS}, 0)
          + COALESCE(p_comments  * {W_COMMENTS},  0)
          + COALESCE(p_hits      * {W_HITS},      0))                      AS s_abs,
           -- Rate view: only the two intent signals, which are the ones worth
           -- comparing per unit time. Comments scale with chapters and hits with
           -- re-reads, so neither says much about pace.
           (CASE WHEN r_kudos     IS NOT NULL THEN {W_KUDOS}     ELSE 0 END
          + CASE WHEN r_bookmarks IS NOT NULL THEN {W_BOOKMARKS} ELSE 0 END) AS w_rate,
           (COALESCE(r_kudos     * {W_KUDOS},     0)
          + COALESCE(r_bookmarks * {W_BOOKMARKS}, 0))                        AS s_rate
      FROM pct
),
final AS (
    SELECT id,
           -- Renormalise over observed weight, shrink toward the prior by how
           -- much of the picture was actually visible, then let the rate view
           -- argue for a quarter of the result where it exists.
           (
             0.75 * ({PRIOR} + (s_abs / NULLIF(w_have, 0) - {PRIOR}) * w_have)
           + 0.25 * CASE WHEN w_rate > 0
                         THEN {PRIOR} + (s_rate / w_rate - {PRIOR}) * w_rate
                         -- No usable date: the absolute view carries the whole
                         -- score rather than being diluted by a neutral 0.5.
                         ELSE {PRIOR} + (s_abs / NULLIF(w_have, 0) - {PRIOR}) * w_have
                    END
           ) AS popularity
      FROM blended
)
UPDATE stories s
   SET popularity = round(f.popularity::numeric, 6)
  FROM final f
 WHERE s.id = f.id
   AND (s.popularity IS DISTINCT FROM round(f.popularity::numeric, 6))
"""


def run(dry_run: bool = False) -> int:
    with db_session() as db:
        # This walks every scored row and writes most of them; the session's
        # 60s default would abort it partway through, leaving the index half
        # ranked on two different runs' worth of percentiles.
        db.execute(text("SET statement_timeout = 0"))
        if dry_run:
            n = db.execute(text("""
                SELECT count(*) FROM stories
                 WHERE delisted_at IS NULL
                   AND (kudos > 0 OR bookmarks > 0 OR comments > 0 OR hits > 0)
            """)).scalar()
            log.info("would score %s works", f"{n:,}")
            return int(n or 0)
        res = db.execute(text(SQL))
        db.commit()
        n = res.rowcount or 0
        log.info("popularity written for %s works", f"{n:,}")
        return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Recompute cross-archive popularity")
    ap.add_argument("--dry-run", action="store_true", help="count candidates, write nothing")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
