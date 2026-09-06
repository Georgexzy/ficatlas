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
import time
from datetime import datetime, timezone

import sqlalchemy
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

# Why temp tables rather than one big WITH ... UPDATE? The plan. On 19.7M rows
# the scored population is ~525k rows, but PostgreSQL estimates it at 1.6M and
# the planner's six-way merge/join cascade on that bad number balloons to
# ~4.79e29 estimated rows, so it picks a plan that runs forever (an hour in, no
# row had been updated). Materialising each percentile into a temp table with a
# PK on id gives the planner exact cardinality and 1:1 join semantics, which is
# the difference between a minute and never.
STAGED = f"""
CREATE TEMP TABLE pr_scored ON COMMIT PRESERVE ROWS AS
    SELECT id, site,
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
;
ANALYZE pr_scored
"""
# One temp table per metric, each filtered to the rows that HAVE that metric,
# rather than one window over everything. This is not tidiness. percent_rank()
# sorts NULLs last, so a single window scored every work missing a figure as if
# it held the highest one in the archive: the first run put FF.net works with
# kudos=0 and comments=0 at the very top of the index, above Harry Potter and
# the Methods of Rationality, with scores over 1.0. A missing metric has to be
# absent from its percentile population, not sorted within it.
#
# PARTITION BY site is the whole parity mechanism: every percentile below is a
# standing among that work's OWN archive, so the three become comparable
# without ever being compared directly.
PERCENTILE_SQL = """
CREATE TEMP TABLE pr_{name} ON COMMIT PRESERVE ROWS AS
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY {expr}) AS v
      FROM pr_scored
     WHERE {where}
;
ALTER TABLE pr_{name} ADD PRIMARY KEY (id);
ANALYZE pr_{name}
"""

# The same standing, per unit of time alive, over the works that have both the
# metric and a credible date.
PERCENTILE_RATE_SQL = """
CREATE TEMP TABLE pr_{name} ON COMMIT PRESERVE ROWS AS
    SELECT id, percent_rank() OVER (PARTITION BY site ORDER BY {expr} / sqrt(age_days)) AS v
      FROM pr_scored
     WHERE {where}
       AND age_days IS NOT NULL
;
ALTER TABLE pr_{name} ADD PRIMARY KEY (id);
ANALYZE pr_{name}
"""

BLENDED_SQL = f"""
CREATE TEMP TABLE pr_blended ON COMMIT PRESERVE ROWS AS
    SELECT s.id, s.site,
           -- Weight actually observed, so the renormalisation below divides by
           -- what was measurable rather than by the full 1.0.
           (CASE WHEN pk.v IS NOT NULL THEN {W_KUDOS}     ELSE 0 END
          + CASE WHEN pb.v IS NOT NULL THEN {W_BOOKMARKS} ELSE 0 END
          + CASE WHEN pc.v IS NOT NULL THEN {W_COMMENTS}  ELSE 0 END
          + CASE WHEN ph.v IS NOT NULL THEN {W_HITS}      ELSE 0 END) AS w_have,
           (COALESCE(pk.v * {W_KUDOS},     0)
          + COALESCE(pb.v * {W_BOOKMARKS}, 0)
          + COALESCE(pc.v * {W_COMMENTS},  0)
          + COALESCE(ph.v * {W_HITS},      0)) AS s_abs,
           -- Rate view: only the two intent signals, which are the ones worth
           -- comparing per unit time. Comments scale with chapters and hits with
           -- re-reads, so neither says much about pace.
           (CASE WHEN rk.v IS NOT NULL THEN {W_KUDOS}     ELSE 0 END
          + CASE WHEN rb.v IS NOT NULL THEN {W_BOOKMARKS} ELSE 0 END) AS w_rate,
           (COALESCE(rk.v * {W_KUDOS},     0)
          + COALESCE(rb.v * {W_BOOKMARKS}, 0)) AS s_rate
      FROM pr_scored s
      LEFT JOIN pr_pk pk ON pk.id = s.id
      LEFT JOIN pr_pb pb ON pb.id = s.id
      LEFT JOIN pr_pc pc ON pc.id = s.id
      LEFT JOIN pr_ph ph ON ph.id = s.id
      LEFT JOIN pr_rk rk ON rk.id = s.id
      LEFT JOIN pr_rb rb ON rb.id = s.id
;
ALTER TABLE pr_blended ADD PRIMARY KEY (id);
ANALYZE pr_blended
"""

# The most weight any work in this archive manages to carry.
#
# Confidence has to be measured against what the ARCHIVE can offer, not against
# a perfect 1.0, or the shrink silently becomes a per-site penalty for what a
# platform does not publish. FanFiction.net has no view counter at all, so no
# FF.net work can ever hold the 0.15 that hits carries: its ceiling was 0.925
# and it took 0 of the top 1% while AO3 took 597. FictionAlley, holding little
# but hits, was capped at 0.575 and could not reach the top 10% of the index at
# any level of acclaim.
#
# Dividing by the site's own best-case restores the thing this whole file is
# for: a work that carries everything its archive records is fully trusted,
# whichever archive that is. It stays self-correcting too — when the harvester
# starts filling a metric a site was missing, the ceiling rises on its own.
#
# The rate view has the same parity need and the same fix. w_rate is at most
# 0.65 (the two intent signals), so without renormalising, a work whose dates
# ARE recorded — every FF.net and FictionAlley row, nearly none of the AO3 bulk
# import — carries a rate term that multiplies a number it can never fully
# hold: its ceiling was (0.75*1.0 + 0.25*{PRIOR}+0.325) = 0.956, while a
# date-less AO3 work skipped the term entirely and scored 1.0. That put 597
# AO3 works above 0.99 and every archive with good dates beneath them for no
# reason a reader would agree with. Dividing by the site's own best rate
# weight restores the same full-trust ceiling for whichever archive records
# the dates.
# The stored score is the work's STANDING IN ITS OWN ARCHIVE, not the blended
# value. One more percent_rank(), partitioned by site, over the blend.
#
# Without it "Most popular" was AO3 and nothing else. Measured 2026-09-06 over
# 2,402,138 scored works: the top 1,000 held 1,000 AO3 works, 0 FanFiction.net
# and 0 FictionAlley — while AO3 is only 84% of what is scored.
#
# The per-metric percentiles were already per-site, so that was not the fault.
# The BLEND compresses each site differently — confidence shrinkage toward the
# prior, the rate term, and each site's own w_max — and it compresses the small
# archives harder. The 99.9th percentile came out at 0.9973 for AO3, 0.9426 for
# FF.net and 0.7999 for FictionAlley, so no FF.net work could reach the band
# where two million AO3 works were already sitting. A linear rescale of the
# endpoints does not fix it (simulated: 997/2/1) because the difference is in
# the SHAPE of each distribution, not its ends.
#
# Ranking the blend within each site removes the shape entirely: every archive
# is then uniform on 0..1 and contributes to any top-N in proportion to how much
# of it has been scored. Simulated on the same data, top 1,000: 836 AO3, 151
# FF.net, 13 FictionAlley — against populations of 84% / 15% / 1.2%.
#
# This also makes the column mean what this file's docstring has always said it
# means: "Top 1% of AO3 by kudos" and "top 1% of FF.net by favs" being the same
# number is the entire point of the percentile approach.
UPDATE_SQL = f"""
CREATE TEMP TABLE pr_final ON COMMIT PRESERVE ROWS AS
    WITH scored AS (
        SELECT b.id, b.site,
               (0.75 * ({PRIOR} + (s_abs / NULLIF(w_have, 0) - {PRIOR}) * LEAST(w_have / c.w_max, 1.0))
              + 0.25 * CASE WHEN w_rate > 0
                            THEN {PRIOR} + (s_rate / w_rate - {PRIOR})
                                           * LEAST(w_rate / r.r_max, 1.0)
                            ELSE {PRIOR} + (s_abs / NULLIF(w_have, 0) - {PRIOR})
                                           * LEAST(w_have / c.w_max, 1.0)
                        END) AS blend
          FROM pr_blended b
          JOIN (SELECT site, GREATEST(MAX(w_have), 0.0001) AS w_max
                  FROM pr_blended GROUP BY site) c ON c.site = b.site
          JOIN (SELECT site, GREATEST(MAX(w_rate), 0.0001) AS r_max
                  FROM pr_blended GROUP BY site) r ON r.site = b.site
    )
    SELECT id,
           round((percent_rank() OVER (PARTITION BY site ORDER BY blend))::numeric, 6)
             AS popularity
      FROM scored
;
ALTER TABLE pr_final ADD PRIMARY KEY (id);
ANALYZE pr_final;
"""

UPDATE_WRITE_SQL = """
UPDATE stories s
   SET popularity = pf.popularity
  FROM pr_final pf
 WHERE s.id = pf.id
   AND (s.popularity IS DISTINCT FROM pf.popularity)
"""


# Each percentile is keyed by id with a PK, so the planner joins on exact
# cardinality instead of the 1.6M-estimate cascade that never terminates.
PERCENTILES = [
    ("pk", PERCENTILE_SQL.format(name="pk", expr="kudos",     where="kudos IS NOT NULL")),
    ("pb", PERCENTILE_SQL.format(name="pb", expr="bookmarks", where="bookmarks IS NOT NULL")),
    ("pc", PERCENTILE_SQL.format(name="pc", expr="comments",  where="comments IS NOT NULL")),
    ("ph", PERCENTILE_SQL.format(name="ph", expr="hits",      where="hits IS NOT NULL")),
    ("rk", PERCENTILE_RATE_SQL.format(name="rk", expr="kudos",     where="kudos IS NOT NULL")),
    ("rb", PERCENTILE_RATE_SQL.format(name="rb", expr="bookmarks", where="bookmarks IS NOT NULL")),
]


# What this pass left behind, for the admin panel's Background jobs section.
#
# That panel exists because THIS script sat frozen for months and nothing
# anywhere showed it — and it still had no row for this job, so the same thing
# happened again quietly: 549,515 works scored against 2,399,048 carrying an
# engagement figure, because the weekly loop's only trace was one line in a
# 75,000-line worker log.
#
# Two numbers, not a heartbeat. The timestamp says the pass finished; the pair
# says whether it is KEEPING UP, which is the failure that actually occurred —
# a job that runs on time and falls further behind every week as the crawler
# enriches rows looks identical to a healthy one from a timestamp alone. Both
# are computed here because this is the only place they are already known;
# counting eligible rows on the panel would be a sequential scan of 20M.
#
# Keys: popularity_built_at, popularity_scored, popularity_eligible. The panel
# reads the first as its evidence timestamp and the last two as a backlog.
_UPSERT = text("""
    INSERT INTO app_settings (key, value) VALUES (:k, :v)
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
""")


def _record_evidence(db, scored: int, eligible: int) -> None:
    """`eligible` is counted off `pr_scored` at the top of the run, not here.

    Counting it here is the same predicate over 20M rows, and it ran at the one
    moment in the pass when the session could no longer afford it: after the
    write, the `SET statement_timeout = 0` the rest of the job relies on was no
    longer in force, and the count died at 60s — so a pass that had just spent
    3h45m writing 2,402,123 rows recorded no evidence that it had happened.
    `pr_scored` holds exactly the eligible population and is already ANALYZEd.
    """
    try:
        db.execute(text("SET LOCAL statement_timeout = '30s'"))
        for key, value in (("popularity_built_at",
                            datetime.now(timezone.utc).isoformat()),
                           ("popularity_scored", str(int(scored))),
                           ("popularity_eligible", str(int(eligible)))):
            db.execute(_UPSERT, {"k": key, "v": value})
        db.commit()
    except Exception as e:
        # Evidence is not the job. A panel row is not worth failing a pass that
        # has already written 2.4M rows.
        db.rollback()
        log.warning("could not record popularity evidence: %s", e)


def run(dry_run: bool = False) -> int:
    with db_session() as db:
        # This walks every scored row and writes most of them; the session's
        # 60s default would abort it partway through, leaving the index half
        # ranked on two different runs' worth of percentiles.
        db.execute(text("SET statement_timeout = 0"))
        # The sort heavy lifting is a few window functions over ~525k scored
        # rows. With the default 32MB work_mem every sort spills to disk; on a
        # memory-tight box that turned a two-minute job into an hour of swap
        # thrash. One-off linear scans, so a generous single-session limit is
        # right here — it never touches the shared buffers of the API workers.
        db.execute(text("SET work_mem = '1GB'"))
        if dry_run:
            n = db.execute(text("""
                SELECT count(*) FROM stories
                 WHERE delisted_at IS NULL
                   AND (kudos > 0 OR bookmarks > 0 OR comments > 0 OR hits > 0)
            """)).scalar()
            log.info("would score %s works", f"{n:,}")
            return int(n or 0)
        db.execute(text(STAGED))
        # The eligible population, counted once off the staging table while it
        # is cheap and while the session still has no statement timeout. See
        # _record_evidence for what happens when this is left until the end.
        eligible = int(db.execute(
            text("SELECT count(*) FROM pr_scored")).scalar() or 0)
        for _, sql in PERCENTILES:
            db.execute(text(sql))
        db.execute(text(BLENDED_SQL))
        db.execute(text(UPDATE_SQL))  # builds pr_final
        db.commit()
        # Force a merge-friendly plan on the final write: drive the UPDATE with
        # an order-preserving index scan over stories in id order rather than a
        # hash join that has to probe every heap page, and a hash of the small
        # pr_final side. On a cold, swap-starved box the default nested-loop
        # plan did ~546k random single-row heap reads (20+ min, zero writes)
        # and a seq-scan hash join streamed the whole 19.7M-row heap in read
        # order (hours). The id-ordered index-scan join reads the heap
        # sequentially and writes matched rows in WAL-friendly order.
        db.execute(text("SET enable_nestloop = off"))

        # The worker is out there updating the same story rows one at a time,
        # and it locks them in a different order than this bulk pass, so the
        # UPDATE can and does deadlock with it. pr_final is already committed
        # (temp tables survive on COMMIT PRESERVE ROWS), so just re-run the
        # UPDATE in a fresh transaction until it wins. The deadlock checks both
        # lock orders, so the retry is safe regardless of which side got aborted.
        for attempt in range(1, 11):
            try:
                res = db.execute(text(UPDATE_WRITE_SQL))
                db.commit()
                n = res.rowcount or 0
                _record_evidence(db, n, eligible)
                log.info("popularity written for %s works (attempt %s)", f"{n:,}", attempt)
                return n
            except sqlalchemy.exc.OperationalError as e:
                if attempt == 10 or "DeadlockDetected" not in str(e):
                    raise
                db.rollback()
                log.warning("deadlock with worker, retrying (%s/10)", attempt)
                time.sleep(2)
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Recompute cross-archive popularity")
    ap.add_argument("--dry-run", action="store_true", help="count candidates, write nothing")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
