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
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

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


def run(dry_run: bool, batch: int = 20000, limit: int | None = None) -> int:
    if not os.path.exists(DB_PATH):
        log.error(f"{DB_PATH} not found — download it first "
                  f"(archive.org/details/fanfic-meta-sqlite)")
        return 2

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        'SELECT "Story URL" AS url, Genre, Published, Updated FROM metadata_full')

    seen = matched = filled_genres = filled_dates = 0
    pending: list[tuple] = []

    def flush(rows: list[tuple]) -> tuple[int, int]:
        """Apply one batch. Returns (genre_writes, date_writes)."""
        if not rows or dry_run:
            return (0, 0)
        with db_session() as db:
            res = db.execute(sql_text("""
                UPDATE stories AS s SET
                    -- coalesce/nullif so an existing value always wins: this can
                    -- only ever add, never replace.
                    genres = CASE WHEN (s.genres IS NULL OR s.genres = '{}')
                                  THEN v.genres ELSE s.genres END,
                    published_at = COALESCE(s.published_at, v.pub),
                    updated_at   = COALESCE(s.updated_at, v.upd)
                FROM (
                    SELECT unnest(:ids) AS site_id,
                           unnest(:genres) AS genres,
                           unnest(:pubs)::timestamptz AS pub,
                           unnest(:upds)::timestamptz AS upd
                ) AS v
                WHERE s.site = 'ffnet' AND s.site_id = v.site_id
                  AND (s.genres IS NULL OR s.genres = '{}'
                       OR s.published_at IS NULL OR s.updated_at IS NULL)
            """), {
                "ids":    [r[0] for r in rows],
                "genres": [r[1] for r in rows],
                "pubs":   [r[2] for r in rows],
                "upds":   [r[3] for r in rows],
            })
            db.commit()
            return (res.rowcount or 0, 0)

    for row in cur:
        seen += 1
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
            flush(pending)
            pending.clear()
            log.info(f"  {seen:,} read, {matched:,} usable "
                     f"({filled_genres:,} with genres, {filled_dates:,} with dates)")
        if limit and seen >= limit:
            break

    flush(pending)
    con.close()

    verb = "would fill" if dry_run else "filled"
    log.info(f"DONE — {seen:,} rows read, {matched:,} usable; "
             f"{verb} genres for {filled_genres:,} and dates for {filled_dates:,}")

    if not dry_run:
        with db_session() as db:
            for label, cond in (("no genres", "genres IS NULL OR genres='{}'"),
                                ("no published date", "published_at IS NULL")):
                n = db.execute(sql_text(
                    f"SELECT count(*) FROM stories WHERE site='ffnet' AND ({cond})")).scalar()
                log.info(f"  remaining {label}: {n:,}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill FF.net genres/dates from the archive.org dump")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="stop after N source rows")
    ap.add_argument("--batch", type=int, default=20000)
    args = ap.parse_args()
    return run(args.dry_run, args.batch, args.limit)


if __name__ == "__main__":
    sys.exit(main())
