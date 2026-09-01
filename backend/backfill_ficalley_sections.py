r"""
Backfill FictionAlley's archive sections from the original dump.
================================================================

FictionAlley was five archives behind one banner, and that is how readers used
it: Schnoogle for novel-length work, The Dark Arts for horror and darkfic, the
Astronomy Tower for romance, Riddikulus for humour, and a smaller section of
essays and meta. Asking for "a Schnoogle fic" meant something specific.

The dump carries this in `stories.site`. The original import read every other
column and dropped that one, so 29,949 works arrived without the distinction
that organised the entire archive.

Local join, no crawling — the source database is still on this machine as
`ficalley_tmp`, matched on the same author_id/story_id pair the import used to
build each URL.

    docker compose exec backend python backfill_ficalley_sections.py --dry-run
    docker compose exec backend python backfill_ficalley_sections.py
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402 — needs the sys.path above
os.environ.setdefault("DATABASE_URL", default_database_url())

import psycopg2
import psycopg2.extras
from sqlalchemy import text as sql_text

from db.session import db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# The abbreviations as the dump stores them, expanded to what readers called
# them. HIP is left descriptive: the works under it are essays, meta and
# analysis — uniquely carrying the Essay and Meta genres — but the acronym's
# expansion is not something the data states, and inventing one would be worse
# than being plain about what it holds.
SECTIONS = {
    "Sch": "Schnoogle",
    "TDA": "The Dark Arts",
    "AT":  "Astronomy Tower",
    "Rid": "Riddikulus",
    "HIP": "Essays & Meta",
}

# Derived from DATABASE_URL rather than hardcoded: the database password is
# rotated for deployment, and a literal here would have kept working right up
# until it silently did not.
SRC_DSN = os.getenv("FICALLEY_SRC_DSN") or (
    os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/ficalley_tmp"
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill FictionAlley archive sections")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = psycopg2.connect(SRC_DSN)
    cur = src.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT author_id, story_id, site FROM stories WHERE site IS NOT NULL AND site <> ''")
    rows = cur.fetchall()
    log.info(f"{len(rows):,} rows with a section in the source dump")

    # site_id is built as fa_<author_id>_<story_id> by the importer.
    mapping = {}
    unknown = set()
    for r in rows:
        label = SECTIONS.get(r["site"])
        if not label:
            unknown.add(r["site"])
            continue
        mapping[f"fa_{r['author_id']}_{r['story_id']}"] = label
    if unknown:
        log.warning(f"unmapped section codes, left alone: {sorted(unknown)}")

    log.info(f"{len(mapping):,} works mapped to a named section")
    if args.dry_run:
        from collections import Counter
        for k, v in Counter(mapping.values()).most_common():
            log.info(f"  would set {v:>7,}  {k}")
        return 0

    updated = 0
    items = list(mapping.items())
    with db_session() as db:
        for i in range(0, len(items), 1000):
            chunk = items[i:i + 1000]
            # One statement per batch rather than per row: 30k round trips for a
            # single column is minutes of latency for no reason.
            db.execute(sql_text("""
                UPDATE stories AS s SET archive_section = v.section
                FROM (SELECT unnest(:ids) AS site_id, unnest(:secs) AS section) AS v
                WHERE s.site = 'fictionalley' AND s.site_id = v.site_id
            """), {"ids": [c[0] for c in chunk], "secs": [c[1] for c in chunk]})
            updated += len(chunk)
        db.commit()

    with db_session() as db:
        got = db.execute(sql_text("""
            SELECT archive_section, count(*) FROM stories
            WHERE site='fictionalley' AND archive_section IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC
        """)).fetchall()
    for name, n in got:
        log.info(f"  {n:>7,}  {name}")
    log.info(f"DONE — {updated:,} rows considered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
