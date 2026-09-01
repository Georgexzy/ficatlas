r"""
Persist the chapter titles recovered from chapter bodies.
=========================================================

The reader derives a better title at serve time — a chapter stored as
"Chapter 01" whose body opens "Chapter 1 - The Competition" is shown as "The
Competition". The chapter LIST on the story page does not, so the same chapter
reads "Chapter 01" in the list and "The Competition" once opened.

Deriving it in the list endpoint too would mean parsing every chapter's HTML on
every page load — 199 parses for one of the fics here. The derivation is
deterministic, so it belongs in the database instead: this walks the chapters
once and writes the recovered titles.

Serve-time derivation stays for anything imported afterwards; this only removes
the need to re-derive what is already known.

    docker compose exec backend python backfill_chapter_titles.py --dry-run
    docker compose exec backend python backfill_chapter_titles.py
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402 — needs the sys.path above
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text as sql_text

from db.session import db_session
from html_sanitize import (is_generic_title, sanitize_html, strip_chapter_heading,
                           tidy_chapter_html)
from models.story import Chapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Only chapters whose stored title says nothing are worth re-deriving; a real
# title must never be overwritten by a guess from the body.
CANDIDATE_SQL = sql_text("""
    SELECT c.id, c.title, c.content, s.title AS story_title
    FROM chapters c
    JOIN stories s ON s.id = c.story_id
    WHERE c.content IS NOT NULL AND c.content <> ''
    ORDER BY c.story_id, c.number
    LIMIT :lim OFFSET :off
""")


def run(dry_run: bool, batch: int = 500) -> None:
    offset = updated = examined = 0
    while True:
        with db_session() as db:
            rows = db.execute(CANDIDATE_SQL, {"lim": batch, "off": offset}).fetchall()
        if not rows:
            break
        offset += len(rows)

        pending: list[tuple] = []
        for cid, title, content, story_title in rows:
            examined += 1
            if not is_generic_title(title):
                continue          # a real title already; never overwrite it
            body = tidy_chapter_html(sanitize_html(content))
            _, better = strip_chapter_heading(body, title, story_title)
            if better and better.strip() and better.strip() != (title or "").strip():
                pending.append((cid, better.strip()[:300]))

        if pending and not dry_run:
            with db_session() as db:
                for cid, new_title in pending:
                    ch = db.query(Chapter).filter(Chapter.id == cid).first()
                    if ch:
                        ch.title = new_title
                db.commit()
        updated += len(pending)

        if dry_run and pending:
            for _, t in pending[:5]:
                log.info(f"    would set: {t!r}")
        log.info(f"  {examined} examined, {updated} titles recovered")

    verb = "would update" if dry_run else "updated"
    log.info(f"DONE — {examined} chapters examined, {verb} {updated}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Persist chapter titles found in chapter bodies")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args()
    run(args.dry_run, args.batch)


if __name__ == "__main__":
    main()
