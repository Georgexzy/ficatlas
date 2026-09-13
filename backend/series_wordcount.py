"""How long is the whole series? Denormalised onto each work.

    docker exec ficatlas-worker-1 python series_wordcount.py --dry-run
    docker exec -d ficatlas-worker-1 sh -c \
        "cd /app && python -u series_wordcount.py > /tmp/series_wc.log 2>&1"

Why the column exists
---------------------
A reader who asks for 150k+ wants something to disappear into, and an author
who told one story across three 60k works has written it. The length is real;
it is simply recorded across rows. Measured at a 150k floor: 18,077 series of
more than one work qualify, holding 158,059 works, and **147,929 of those are
individually shorter** — so without this they cannot be found by anybody asking
for a long read.

Why it is a COLUMN and not a join
---------------------------------
The obvious form is a semi-join OR-ed onto the length filter:

    word_count >= :n OR id IN (SELECT story_id FROM series_works …)

which cannot use an index for either side and measured **16.2s against 0.9s**
on `tag:"Time Travel" words:>150k`, with a second query timing out at 30s.
Beside `word_count` on the same table it is a BitmapOr of two index scans.

What is deliberately left NULL
------------------------------
A standalone, and a one-work series. 75,622 one-work series are REAL — authors
file a standalone in a series, or intend to add more — and for those the total
is just that one work's own length, so filling it in would mean applying the
same filter twice and calling it a feature. `member_count > 1` is also the
predicate of `ix_series_total_words`, so the guard costs nothing.

Batched, and this is the lesson content_gates.py learned the hard way: a job
that rewrites a million rows WILL be interrupted, and should cost only the work
not yet done. Here the progress marker is the column itself — a row already
carrying the right value is excluded by the `IS DISTINCT FROM` predicate.
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

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("series_wordcount")

BATCH = int(os.getenv("SERIES_WC_BATCH", "50000"))
PAUSE_S = float(os.getenv("SERIES_WC_PAUSE_S", "5"))

# One run at a time, for the same reason as content_gates.py: two passes
# rewriting the same rows in different orders is the textbook deadlock, and it
# has already cost this box an outage once.
_LOCK_KEY = 0x5E21E5CD

# A work that has LEFT a qualifying series — the series was merged, pruned, or
# fell to one work — must lose the value, or the column can only ever grow
# staler in the direction that admits works the reader did not ask for.
_CLEAR_SQL = """
UPDATE stories SET series_total_words = NULL
 WHERE id IN (SELECT id FROM stories
               WHERE series_total_words IS NOT NULL
                 AND NOT EXISTS (
                       SELECT 1 FROM series_works sw
                         JOIN series se ON se.id = sw.series_id
                        WHERE sw.story_id = stories.id
                          AND se.member_count > 1
                          AND se.total_words IS NOT NULL)
               LIMIT :batch)
"""

# The max above is not arbitrary: a work can sit in more than one series (a
# sequel that is also part of an omnibus), and the reader asking for a long
# read is served by the longest of them.
# A KEYSET cursor over series_works, and the first draft is worth recording
# because it looked right and was quadratic.
#
# That version drew each batch from "rows that are currently wrong", which does
# terminate and does resume — but the planner satisfies the LIMIT by walking
# `ix_series_works_story` from the beginning every time, probing `stories` by
# primary key and discarding rows it has already fixed. Batch 1 probes 50,000
# rows; batch 21 probes a million to find the last 50,000. Measured at four
# minutes for the FIRST batch, and every batch after it slower.
#
# Walking the index once, from a high-water mark, is O(n). The window is a
# slice of series_works ordered by story_id; `IS DISTINCT FROM` still decides
# what is actually written, so a re-run over already-correct rows costs the
# walk and no writes. The loop continues while the WINDOW yielded rows, not
# while the UPDATE changed any — otherwise a batch of already-correct rows
# would look like the end of the table.
#
# `max()` is not arbitrary: a work can sit in more than one series (a sequel
# that is also part of an omnibus), and a reader asking for a long read is
# served by the longest of them.
_FILL_SQL = """
WITH todo AS (
    SELECT sw.story_id AS id, max(se.total_words) AS total
      FROM series_works sw
      JOIN series se ON se.id = sw.series_id
     WHERE se.member_count > 1
       AND se.total_words IS NOT NULL
       AND sw.story_id > CAST(:after AS uuid)
     GROUP BY sw.story_id
     ORDER BY sw.story_id
     LIMIT :batch
), upd AS (
    UPDATE stories st SET series_total_words = t.total
      FROM todo t
     WHERE st.id = t.id
       AND st.series_total_words IS DISTINCT FROM t.total
    RETURNING 1
)
-- No max(uuid) in Postgres, and the window is already ordered by id, so the
-- last row IS the high-water mark.
SELECT (SELECT id FROM todo ORDER BY id DESC LIMIT 1) AS next_after,
       (SELECT count(*)  FROM todo) AS seen,
       (SELECT count(*)  FROM upd)  AS changed
"""


def run(dry_run: bool = False) -> dict:
    with db_session() as db:
        if not dry_run and not db.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            log.warning("series_wordcount: another run holds the lock; skipping")
            return {"skipped": True}

        # Not a request: the pooled statement timeout is tuned so a slow SEARCH
        # fails fast, and inheriting it here means the pass can never finish.
        db.execute(text("SET statement_timeout = 0"))

        if dry_run:
            row = db.execute(text("""
                SELECT count(*) FROM series_works sw
                  JOIN series se ON se.id = sw.series_id
                 WHERE se.member_count > 1 AND se.total_words IS NOT NULL
            """)).first()
            log.info("series_wordcount: %s member works of multi-work series",
                     f"{row[0]:,}")
            return {"eligible": row[0]}

        filled, seen_total = 0, 0
        # The zero UUID, so the first window starts before every story id.
        after = "00000000-0000-0000-0000-000000000000"
        while True:
            row = db.execute(text(_FILL_SQL),
                             {"after": after, "batch": BATCH}).first()
            db.commit()
            next_after, seen, changed = row[0], int(row[1]), int(row[2])
            if not seen:
                break
            after = str(next_after)
            filled += changed
            seen_total += seen
            log.info("series_wordcount: %s seen, %s written (%s / %s)",
                     f"{seen:,}", f"{changed:,}", f"{seen_total:,}", f"{filled:,}")
            time.sleep(PAUSE_S)

        cleared = 0
        while True:
            n = db.execute(text(_CLEAR_SQL), {"batch": BATCH}).rowcount
            db.commit()
            if not n:
                break
            cleared += n
            log.info("series_wordcount: cleared %s (%s so far)",
                     f"{n:,}", f"{cleared:,}")
            time.sleep(PAUSE_S)

        carrying = db.execute(text(
            "SELECT count(*) FROM stories WHERE series_total_words IS NOT NULL"
        )).scalar()

    log.info("series_wordcount: %s works carry a series total (%s filled, "
             "%s cleared)", f"{carrying:,}", f"{filled:,}", f"{cleared:,}")
    return {"carrying": carrying, "filled": filled, "cleared": cleared}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
