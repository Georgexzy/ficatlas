r"""
Fill FF.net genres and dates from the archive.org metadata dump.
================================================================

6,570,332 of our 6,572,234 FF.net rows have no genres, and 6,571,275 have no
published date — not because the importer dropped them, but because the
HuggingFace dump they came from has eight columns and neither field is among
them (source_file, category, rating, chapters, words, story_url, summary,
language). No amount of re-importing that file produces what is not in it.

archive.org/details/fanfic-meta-sqlite does carry them. It is a 7.2GB SQLite
database of FanFiction.net metadata, and its one table has:

    Path, Title, Author, Category, Genre, Language, Status, Published, Updated,
    Packaged, Rating, Chapters, Words, Publisher, `Story URL`, `Author URL`,
    Summary, word_count, chapter_count

Matched on the FF.net story id parsed out of `Story URL`, which is the same id
our own site_id holds.

Deliberately additive only
--------------------------
Every write is guarded so a field is filled ONLY where ours is empty. The dump
is from 2019 and ours has been enriched since from Wayback captures; letting a
six-year-old row overwrite a fresher one would be a downgrade dressed as an
import. Nothing here can reduce what the index already knows.

Summaries are deliberately NOT imported. Ours are 99.998% complete (131 rows
short of 6.57M), and carrying 20,000 summaries per batch to fill a hundred rows
was most of the payload — big enough that the UPDATE's hash join OOM-killed the
Postgres backend on a 14GB host and took the database down with it. The 131 are
not worth a gigabyte of churn.

Genres arrive as FF.net writes them — "Romance/Angst", at most two — so they are
split on "/" into our array.

    docker compose exec backend python ffnet_meta_sqlite_importer.py --dry-run
    docker compose exec backend python ffnet_meta_sqlite_importer.py
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402 — needs the sys.path above
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text as sql_text

from db.session import db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = os.getenv("FFN_SQLITE", "/data/ffnmeta.sqlite")
_ID_RE = re.compile(r"fanfiction\.net/s/(\d+)")

# FF.net's own genre vocabulary. Anything outside it is dropped rather than
# stored: the column occasionally holds stray text, and a freeform value would
# pollute the facet list that drives autocomplete for every other search.
FFN_GENRES = {
    "Adventure", "Angst", "Crime", "Drama", "Family", "Fantasy", "Friendship",
    "General", "Horror", "Humor", "Hurt/Comfort", "Mystery", "Parody", "Poetry",
    "Romance", "Sci-Fi", "Spiritual", "Supernatural", "Suspense", "Tragedy",
    "Western",
}


def parse_genres(raw: str | None) -> list[str]:
    if not raw:
        return []
    # "Hurt/Comfort" contains the separator, so it is matched before splitting.
    text = raw.strip()
    out: list[str] = []
    if "Hurt/Comfort" in text:
        out.append("Hurt/Comfort")
        text = text.replace("Hurt/Comfort", "")
    for part in text.split("/"):
        p = part.strip()
        if p in FFN_GENRES and p not in out:
            out.append(p)
    return out


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw[:19] if " " in raw else raw, fmt)
        except ValueError:
            continue
    return None


def run(dry_run: bool, batch: int = 5000, limit: int | None = None,
        skip: int = 0) -> int:
    if not os.path.exists(DB_PATH):
        log.error(f"{DB_PATH} not found — download it first "
                  f"(archive.org/details/fanfic-meta-sqlite)")
        return 2

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.execute('SELECT "Story URL" AS url, Genre, Published, Updated '
                      'FROM metadata_full')

    seen = matched = filled_genres = filled_dates = written = 0
    pending: list[tuple] = []

    def flush(rows: list[tuple]) -> int:
        """Apply one batch. Returns the number of rows the UPDATE actually touched."""
        if not rows or dry_run:
            return 0
        with db_session() as db:
            res = db.execute(sql_text("""
                UPDATE stories AS s SET
                    -- Each write is guarded so a field is filled ONLY where ours
                    -- is empty. This can add, never replace.
                    genres = CASE WHEN (s.genres IS NULL OR s.genres = '{}')
                                  THEN COALESCE(v.genres, s.genres) ELSE s.genres END,
                    published_at = COALESCE(s.published_at, v.pub),
                    updated_at   = COALESCE(s.updated_at, v.upd)
                FROM (
                    -- Every column cast explicitly. Postgres cannot infer the
                    -- type of an empty array, and most source rows have no
                    -- genres — so an untyped parameter failed the whole batch
                    -- with "cannot determine type of empty array".
                    --
                    -- Genres arrive as one delimited string per row rather than
                    -- a list of lists: Postgres arrays are rectangular, so a
                    -- ragged list of per-row genre lists is not expressible as
                    -- text[][] at all.
                    SELECT unnest(CAST(:ids AS text[])) AS site_id,
                           string_to_array(
                             nullif(unnest(CAST(:genres AS text[])), ''), '|') AS genres,
                           unnest(CAST(:pubs AS timestamptz[])) AS pub,
                           unnest(CAST(:upds AS timestamptz[])) AS upd
                ) AS v
                WHERE s.site = 'ffnet' AND s.site_id = v.site_id
                  AND (s.genres IS NULL OR s.genres = '{}'
                       OR s.published_at IS NULL OR s.updated_at IS NULL)
            """), {
                "ids":       [r[0] for r in rows],
                "genres":    ["|".join(r[1]) for r in rows],
                "pubs":      [r[2] for r in rows],
                "upds":      [r[3] for r in rows],
            })
            db.commit()
            return res.rowcount or 0

    for row in cur:
        seen += 1
        # SQLite returns rows in a stable order for an unordered scan of the
        # same file, so counting past the first N is a sound resume. Cheap
        # enough to be worth it: skipping is a tight loop, redoing the work is
        # an hour of UPDATEs that all match zero rows.
        if seen <= skip:
            continue
        m = _ID_RE.search(row["url"] or "")
        if not m:
            continue
        genres = parse_genres(row["Genre"])
        pub = parse_date(row["Published"])
        upd = parse_date(row["Updated"])
        if not genres and not pub and not upd:
            continue
        matched += 1
        pending.append((m.group(1), genres, pub, upd))
        if genres:
            filled_genres += 1
        if pub or upd:
            filled_dates += 1

        if len(pending) >= batch:
            written += flush(pending)
            pending.clear()
            # `written` is rows the UPDATE actually changed. The other two count
            # what the DUMP offered, which is a much larger number — most source
            # rows describe stories this index has never heard of. Reporting the
            # offered figure as though it were the applied one would have
            # claimed millions of fills on a run that changed nothing.
            log.info(f"  {seen:,} read · {matched:,} usable · {written:,} rows updated")
        if limit and seen >= limit:
            break

    written += flush(pending)
    con.close()

    if dry_run:
        log.info(f"DRY RUN — {seen:,} rows read, {matched:,} carry something usable; "
                 f"nothing written")
    else:
        log.info(f"DONE — {seen:,} rows read, {matched:,} usable, "
                 f"{written:,} of our rows updated")

    if not dry_run:
        with db_session() as db:
            for label, cond in (("no genres", "genres IS NULL OR genres='{}'"),
                                ("no published date", "published_at IS NULL"),
                                ("no updated date", "updated_at IS NULL")):
                n = db.execute(sql_text(
                    f"SELECT count(*) FROM stories WHERE site='ffnet' AND ({cond})")).scalar()
                log.info(f"  remaining {label}: {n:,}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill FF.net genres/dates from the archive.org dump")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="stop after N source rows")
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--skip", type=int, default=0,
                    help="skip the first N source rows (to resume)")
    args = ap.parse_args()
    return run(args.dry_run, args.batch, args.limit, args.skip)


if __name__ == "__main__":
    sys.exit(main())
