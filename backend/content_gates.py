"""What the site will not put in front of somebody who did not ask for it.

    docker exec ficatlas-backend-1 python content_gates.py --dry-run
    docker exec ficatlas-backend-1 python content_gates.py

Why this exists
---------------
A reader was banned for fourteen days from r/HPFanfiction for linking a
FicAtlas search that listed works tagged "Underage Sex". The Explicit toggle
was off and did nothing, because it filtered on RATING and those works were
rated M and Not Rated — AO3's warnings and tags are orthogonal to the rating,
and the two had never been connected.

The principle is not censorship. Nothing is removed from the index: this is an
index of what the archives hold, and a reader who deliberately asks for
something the archives themselves label is entitled to find it. What the site
will not do is put it in front of somebody who did not ask, or bake it into a
URL they then paste in public, where they carry the consequences.

Why it is precomputed
---------------------
Because the alternative was measured and was not viable. Excluding this at
query time means `NOT (tags && ARRAY[…])`, and a NEGATED containment cannot use
the GIN index — every candidate row is tested individually. With both tiers
live, `harry potter` went from 2.4s to 11.4s and `drarry` from 2.6s to 9.4s,
which under load is a 503 and a reader who sees nothing at all. A filter that
breaks the site is not a safety feature.

Two indexed booleans cost the query nothing, and that is what makes the lists
below affordable: being thorough is free once the work is done offline.

Tiers
-----
    gate_underage   sexual content involving minors. Behind its OWN parameter
                    (`include_underage`), never unlocked by `explicit`, and
                    never set by anything that generates a shareable link.
    gate_adult      explicit sex, and the deliberately disturbing. Behind the
                    ordinary Explicit toggle.

Exact values, never substrings
------------------------------
`Underage Drinking` is on 25,321 works, `Underage Smoking` 7,655,
`Non-Consensual Drug Use` 18,580 — none of which is what any of this is about —
and a `chan%` pattern catches `Chance Meetings`. Over-blocking hides tens of
thousands of ordinary stories and teaches people the filter is broken, which
is how a safety feature ends up switched off.
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

# The lists themselves live in gate_terms.py, which is imported by BOTH this
# module (which owns the trigger) and api/search.py (which owns the query
# filters). They used to be written out in both places and had drifted 15 and
# 31 terms apart, with the search copy the laxer one — see gate_terms.py.
from gate_terms import (ADULT_TAGS, ADULT_WARNINGS,  # noqa: E402
                        UNDERAGE_TAGS, UNDERAGE_WARNINGS)
from sqlalchemy import text  # noqa: E402

from db.session import db_session, lift_statement_timeout  # noqa: E402

log = logging.getLogger("content_gates")

# ── The trigger: why a batch job alone is not enough ────────────────────────
#
# The crawler adds ~15,000 works a day. A precomputed column populated by a
# periodic job is therefore STALE by construction, and it is stale in the worst
# possible direction: the column defaults to `false`, so a row that has not
# been processed yet reads as safe. A safety feature whose failure mode is
# "shows the content" is not one.
#
# So the database sets it on write. A trigger cannot be forgotten by a new
# importer, cannot be skipped by a bulk COPY that nobody remembered to tell,
# and runs inside the same transaction as the insert — there is no window in
# which a row exists ungated.
#
# The Python lists above stay the single source of truth: this function is
# REGENERATED from them on every run, so changing a list and running the job
# changes both the backfill and the trigger together. Two hand-maintained
# copies of what counts as this content would drift, and the one that drifts
# laxer is the bug.
_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION fic_set_content_gates() RETURNS trigger AS $$
BEGIN
    NEW.gate_underage := (
        COALESCE(NEW.warnings, '{}') && CAST(:uw AS text[])
     OR COALESCE(NEW.tags,     '{}') && CAST(:ut AS text[]));
    NEW.gate_adult := (
        COALESCE(NEW.warnings, '{}') && CAST(:aw AS text[])
     OR COALESCE(NEW.tags,     '{}') && CAST(:at AS text[]));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_content_gates ON stories;
CREATE TRIGGER trg_content_gates
    BEFORE INSERT OR UPDATE OF tags, warnings ON stories
    FOR EACH ROW EXECUTE FUNCTION fic_set_content_gates();
"""


def install_trigger(db) -> None:
    """Regenerate the write-time gate from the lists above."""
    def _arr(values: list[str]) -> str:
        inner = ",".join("'" + v.replace("'", "''") + "'" for v in values)
        return "ARRAY[" + inner + "]::text[]"
    db.execute(text(_TRIGGER_SQL
                    .replace("CAST(:uw AS text[])", _arr(UNDERAGE_WARNINGS))
                    .replace("CAST(:ut AS text[])", _arr(UNDERAGE_TAGS))
                    .replace("CAST(:aw AS text[])", _arr(ADULT_WARNINGS))
                    .replace("CAST(:at AS text[])", _arr(ADULT_TAGS))))


# POSITIVE containment, which the GIN index can serve.
#
# The first version looked for rows "needing an update" — a NOT-equal over the
# whole table — and timed out after touching nothing. Asking instead for the
# rows that DO carry a flagged tag is an indexed lookup: the flagged population
# is a tiny fraction of 20.5M, and this only has to visit it.
#
# Two statements per tier, because warnings and tags are separate arrays and
# `a && x OR b && y` cannot use either index.
# Bounded, and that is the whole point: see _flag() below.
_FLAG_SQL = """
UPDATE stories SET {col} = true
 WHERE id IN (SELECT id FROM stories
               WHERE {arr} && CAST(:v AS text[]) AND NOT {col}
               LIMIT :batch)
"""

# And the reverse, for when a term is REMOVED from a list: a row that no longer
# matches anything must lose its flag, or the lists can only ever get stricter.
_UNFLAG_SQL = """
UPDATE stories SET {col} = false
 WHERE id IN (SELECT id FROM stories
               WHERE {col}
                 AND NOT (COALESCE(warnings,'{{}}') && CAST(:w AS text[]))
                 AND NOT (COALESCE(tags,'{{}}')     && CAST(:t AS text[]))
               LIMIT :batch)
"""

PARAMS = {"uw": UNDERAGE_WARNINGS, "ut": UNDERAGE_TAGS,
          "aw": ADULT_WARNINGS, "at": ADULT_TAGS}

# Batched, because this touches 20.5M rows and a single statement would hold a
# transaction open for the whole run — on a box that is also serving searches.
BATCH = int(os.getenv("CONTENT_GATE_BATCH", "50000"))
# Seconds between statements. See the note where it is used.
PAUSE_S = float(os.getenv("CONTENT_GATE_PAUSE_S", "5"))


# One run at a time, enforced by the database rather than by whoever is typing.
#
# Two of these were started concurrently by hand and the result was not two
# backfills — it was a deadlock that also blocked the crawler's inserts, ANALYZE,
# the watchdog and the traffic panel, and left the site answering searches in
# sixteen seconds. Each run rewrites hundreds of thousands of rows; two touching
# the same rows in different orders is the textbook case.
#
# An advisory lock is the right shape: held on the connection, so it cannot
# outlive a crash the way a flag in a table could, and pg_try_advisory_lock
# never waits — a second run has nothing useful to do and says so.
_LOCK_KEY = 0x6721C0DE


def run(dry_run: bool = False) -> dict:
    with db_session() as db:
        if not dry_run and not db.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            log.warning("content_gates: another run holds the lock; skipping")
            return {"skipped": True}
        if dry_run:
            row = db.execute(text("""
                SELECT count(*) FILTER (WHERE warnings && CAST(:uw AS text[])
                                           OR tags && CAST(:ut AS text[])) AS ua,
                       count(*) FILTER (WHERE warnings && CAST(:aw AS text[])
                                           OR tags && CAST(:at AS text[])) AS ad
                  FROM stories
            """), PARAMS).first()
            log.info("content_gates: would flag %s underage, %s adult",
                     f"{row[0]:,}", f"{row[1]:,}")
            return {"underage": row[0], "adult": row[1]}

        # The trigger FIRST, so that anything the crawler writes while the
        # backfill below is running is already gated. Doing it the other way
        # round leaves a window the length of the backfill.
        # No statement timeout. This is a maintenance job, not a request: the
        # pooled default is tuned so a slow SEARCH fails fast rather than
        # holding a connection, and inheriting it here just means the backfill
        # can never finish. Set per-session, so nothing else is affected.
        db.execute(text("SET statement_timeout = 0"))

        install_trigger(db)
        db.commit()
        log.info("content_gates: write-time trigger installed")

        # Committed in BATCHES, and this was learned the hard way twice.
        #
        # Each pass was ONE unbounded UPDATE. `gate_adult` via tags is 1.4M
        # rows, so that statement ran for tens of minutes inside a single
        # transaction — and every interruption rolled the whole thing back and
        # left the gate exactly where it started. Three separate runs died that
        # way (the container was restarted under them), each losing everything
        # it had done, while the belt-and-braces array filter in api/search.py
        # stayed in the hot path because the flags could not be trusted.
        #
        # `BATCH` was declared for this from the beginning and never wired in.
        # Batched, an interruption costs the batch, the next run resumes where
        # this one stopped (the predicate IS the progress marker — a flagged row
        # no longer matches `NOT {col}`), and no transaction holds locks on the
        # biggest table for longer than one batch.
        #
        # Same argument as tropedia_recs_import.py's 25-page commits and
        # popularity_rank.py's detached run: a job measured in hours WILL be
        # interrupted, and should cost only the work not yet done.
        def _pass(sql: str, params: dict, what: str) -> int:
            done = 0
            while True:
                lift_statement_timeout(db)
                n = db.execute(text(sql), {**params, "batch": BATCH}).rowcount
                db.commit()
                if not n:
                    # Logged even at zero. A pass with nothing to do is the
                    # NORMAL state once a backfill has caught up, and a log
                    # that goes quiet for it is indistinguishable from one
                    # that has hung — which is the distinction the admin
                    # panel's "evidence, not heartbeats" rule exists to make.
                    log.info("content_gates: %s -> %s rows", what, f"{done:,}")
                    return done
                done += n
                log.info("content_gates: %s -> %s rows (%s so far)",
                         what, f"{n:,}", f"{done:,}")
                # Breathe. This runs on the same box that serves searches; a
                # pause between batches lets the crawler, ANALYZE and autovacuum
                # get a turn instead of queueing behind the whole job.
                time.sleep(PAUSE_S)

        total = 0
        for col, arr, values in (
            ("gate_underage", "warnings", UNDERAGE_WARNINGS),
            ("gate_underage", "tags",     UNDERAGE_TAGS),
            ("gate_adult",    "warnings", ADULT_WARNINGS),
            ("gate_adult",    "tags",     ADULT_TAGS),
        ):
            total += _pass(_FLAG_SQL.format(col=col, arr=arr), {"v": values},
                           f"{col} via {arr}")

        for col, w, t in (("gate_underage", UNDERAGE_WARNINGS, UNDERAGE_TAGS),
                          ("gate_adult",    ADULT_WARNINGS,    ADULT_TAGS)):
            _pass(_UNFLAG_SQL.format(col=col), {"w": w, "t": t},
                  f"{col} cleared")

        counts = db.execute(text(
            "SELECT count(*) FILTER (WHERE gate_underage), "
            "       count(*) FILTER (WHERE gate_adult) FROM stories")).first()
    log.info("content_gates: %s flagged underage, %s flagged adult (%s updated)",
             f"{counts[0]:,}", f"{counts[1]:,}", f"{total:,}")
    return {"underage": counts[0], "adult": counts[1], "updated": total}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
