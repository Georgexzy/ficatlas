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

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("content_gates")

# ── Tier 1: sexual content involving minors ─────────────────────────────────
UNDERAGE_WARNINGS = ["Underage Sex", "Underage"]

UNDERAGE_TAGS = [
    "Underage Sex - Freeform", "Consensual Underage Sex",
    "Underage Rape/Non-con", "Implied/Referenced Underage Sex",
    "Underage - Freeform", "Extremely Underage", "Underage",
    "Underage Sex", "Underage Sexual Activity", "Underage Masturbation",
    "Underage Prostitution", "Underage Pregnancy", "Underage Smut",
    "Minor/Adult Relationship", "Adult/Minor Relationship",
    "Teenage Sexuality", "Underage Drinking and Sex",
    "Pedophilia", "Implied/Referenced Pedophilia", "Pedophile",
    "Grooming", "Child Grooming", "Child Abuse - Sexual",
    "Child Sexual Abuse", "Childhood Sexual Abuse",
    "Shotacon", "Lolicon", "Chan", "Ephebophilia",
    "Statutory Rape", "Underage Non-Consensual",
]

# ── Tier 2: explicit sex, and the deliberately disturbing ───────────────────
ADULT_WARNINGS = ["Rape/Non-Con", "Rape/Non-con"]

ADULT_TAGS = [
    # Explicit sexual content, by its usual names.
    "Smut", "PWP", "Plot What Plot/Porn Without Plot", "Porn With Plot",
    "Porn with Feelings", "Porn", "Pornography", "Explicit Sexual Content",
    "Graphic Depictions of Sex", "Explicit Language and Sexual Content",
    "Rough Sex", "Anal Sex", "Oral Sex", "Vaginal Sex", "Threesome - M/M/F",
    "Threesome - F/F/M", "Orgy", "Gangbang", "Sex Toys", "BDSM",
    "Dubious Consent", "Dub-Con", "Dubcon",
    # AO3's own marker for "this is as unpleasant as it says on the tin".
    "Dead Dove: Do Not Eat", "Dead Dove Do Not Eat",
    # Non-consent.
    "Rape/Non-con Elements", "Rape", "Non-Con", "Noncon", "Non-con",
    "Implied/Referenced Rape/Non-con", "Past Rape/Non-con",
    "Attempted Rape/Non-Con", "Rape/Non-con", "Rape Aftermath",
    "Non-Consensual", "Forced Orgasm", "Sexual Assault",
    "Implied/Referenced Sexual Assault",
    # Incest.
    "Incest", "Sibling Incest", "Parent/Child Incest",
    "Brother/Brother Incest", "Brother/Sister Incest",
    "Sister/Sister Incest", "Twincest", "Implied/Referenced Incest",
    "Parent/Child Relationship", "Family Incest",
    # The rest of the obvious.
    "Bestiality", "Necrophilia", "Cannibalism", "Snuff",
    "Torture", "Graphic Torture", "Mutilation", "Self-Harm",
    "Suicide", "Suicidal Thoughts", "Eating Disorders",
]

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
_FLAG_SQL = """
UPDATE stories SET {col} = true
 WHERE {arr} && CAST(:v AS text[]) AND NOT {col}
"""

# And the reverse, for when a term is REMOVED from a list: a row that no longer
# matches anything must lose its flag, or the lists can only ever get stricter.
_UNFLAG_SQL = """
UPDATE stories SET {col} = false
 WHERE {col}
   AND NOT (COALESCE(warnings,'{{}}') && CAST(:w AS text[]))
   AND NOT (COALESCE(tags,'{{}}')     && CAST(:t AS text[]))
"""

PARAMS = {"uw": UNDERAGE_WARNINGS, "ut": UNDERAGE_TAGS,
          "aw": ADULT_WARNINGS, "at": ADULT_TAGS}

# Batched, because this touches 20.5M rows and a single statement would hold a
# transaction open for the whole run — on a box that is also serving searches.
BATCH = int(os.getenv("CONTENT_GATE_BATCH", "50000"))


def run(dry_run: bool = False) -> dict:
    with db_session() as db:
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

        total = 0
        for col, arr, values in (
            ("gate_underage", "warnings", UNDERAGE_WARNINGS),
            ("gate_underage", "tags",     UNDERAGE_TAGS),
            ("gate_adult",    "warnings", ADULT_WARNINGS),
            ("gate_adult",    "tags",     ADULT_TAGS),
        ):
            n = db.execute(text(_FLAG_SQL.format(col=col, arr=arr)),
                           {"v": values}).rowcount
            db.commit()
            total += n
            log.info("content_gates: %s via %s -> %s rows", col, arr, f"{n:,}")

        for col, w, t in (("gate_underage", UNDERAGE_WARNINGS, UNDERAGE_TAGS),
                          ("gate_adult",    ADULT_WARNINGS,    ADULT_TAGS)):
            n = db.execute(text(_UNFLAG_SQL.format(col=col)),
                           {"w": w, "t": t}).rowcount
            db.commit()
            if n:
                log.info("content_gates: %s cleared on %s rows", col, f"{n:,}")

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
