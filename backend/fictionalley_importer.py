r"""
FictionAlley Archive Importer — full text (stories + chapters)
==========================================================
Imports stories AND their chapter content. Marks stories as is_hosted=True
so the reader page knows to load the text from us, not link out.

Usage from host machine:
    # 1. Restore the dump into a temp DB (one-time):
    docker compose exec -e PGPASSWORD=ficatlas db bash -c '
      psql -U ficatlas -d postgres -c "DROP DATABASE IF EXISTS ficalley_tmp;"
      psql -U ficatlas -d postgres -c "CREATE DATABASE ficalley_tmp;"
      psql -U ficatlas -d postgres -c "DO \$\$ BEGIN CREATE ROLE frank; EXCEPTION WHEN OTHERS THEN NULL; END \$\$;"
      pg_restore -U ficatlas -d ficalley_tmp --no-owner --no-acl /tmp/dump
    '

    # 2. Run the import:
    docker compose exec backend python fictionalley_importer.py
    docker compose exec backend python fictionalley_importer.py --limit 100         # test
    docker compose exec backend python fictionalley_importer.py --no-chapters       # metadata only
"""
import os, sys, argparse, logging
from datetime import datetime

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402 — needs the sys.path above
os.environ.setdefault("DATABASE_URL", default_database_url())

from db.session import db_session
from models.story import Story, Chapter, SiteEnum, RatingEnum, StatusEnum

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TEMP_DB = "ficalley_tmp"

RATING_MAP = {
    "G": RatingEnum.general, "PG": RatingEnum.general,
    "PG-13": RatingEnum.teen, "PG13": RatingEnum.teen,
    "R": RatingEnum.mature,
    "NC-17": RatingEnum.explicit, "NC17": RatingEnum.explicit,
}


def parse_rating(v):
    return RATING_MAP.get(str(v or "").strip().upper(), RatingEnum.not_rated)


def wayback_url_for(story_id: str, author_id: str) -> str:
    """Best-effort Wayback Machine URL for a FictionAlley story."""
    return f"https://web.archive.org/web/2018*/fictionalley.org/authors/{author_id}/{story_id}"


def import_all(limit, include_hidden, include_corrupt, with_chapters):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    # The scratch import database, not the index. Same server, so the same
    # credentials — read from the environment rather than written here.
    src = psycopg2.connect(dsn=default_database_url(dbname=TEMP_DB))
    cur = src.cursor(cursor_factory=RealDictCursor)

    log.info("Loading authors...")
    cur.execute("SELECT author_id, pen_name FROM authors")
    authors = {r["author_id"]: r["pen_name"] for r in cur.fetchall()}
    log.info(f"  {len(authors):,} authors")

    # Existing FictionAlley URLs (skip dupes)
    existing = {}
    log.info("Loading existing FictionAlley stories...")
    with db_session() as db:
        rows = (db.query(Story.id, Story.url)
                .filter(Story.site == SiteEnum.fictionalley).all())
        for sid, url in rows:
            existing[url] = sid
    log.info(f"  {len(existing):,} existing")

    where = []
    if not include_hidden:  where.append("is_hidden = false")
    if not include_corrupt: where.append("is_corrupt = false")
    sql = f"""SELECT * FROM stories
              {('WHERE ' + ' AND '.join(where)) if where else ''}
              {f'LIMIT {limit}' if limit else ''}"""
    cur.execute(sql)
    rows = cur.fetchall()
    log.info(f"  {len(rows):,} stories to process")

    inserted_stories = 0
    skipped_stories = 0
    inserted_chapters = 0
    batch_size = 100

    # Process in batches
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]

        with db_session() as db:
            for row in chunk:
                sid = row["story_id"]
                aid = row["author_id"]
                url = f"https://www.fictionalley.org/authors/{aid}/{sid}"

                if url in existing:
                    skipped_stories += 1
                    continue

                pen = (authors.get(aid) or "Anonymous").strip()
                story = Story(
                    site=SiteEnum.fictionalley,
                    site_id=f"fa_{aid}_{sid}",
                    url=url,
                    title=(row["title"] or f"Story {sid}").strip(),
                    author=pen,
                    author_url=f"https://www.fictionalley.org/authors/{aid}",
                    summary=(row["summary"] or "").strip() or None,
                    language="English",
                    rating=parse_rating(row["rating"]),
                    # The dump carries NO completion signal: is_complete is NOT NULL
                    # and false for all 30,108 rows, so `is_complete ? x : y` reduces
                    # to a constant. Reading it as "unfinished" asserts something the
                    # data does not say, and because a `status=complete` search
                    # excludes in_progress (while treating unknown as permissive),
                    # that assertion HIDES the work. Same defect as 38be858.
                    #
                    # A single-chapter story on an archive that stopped accepting
                    # updates in 2018 is complete in every sense that matters, so that
                    # much is inferable. Anything longer is genuinely unknown.
                    status=(StatusEnum.complete if int(row["chapters"] or 1) == 1
                            else StatusEnum.unknown),
                    word_count=int(row["words"] or 0),
                    chapter_count=int(row["chapters"] or 1),
                    # Only claim a known total for the one-shots above; leaving it
                    # NULL is what signals "we don't know how long this ends up".
                    chapter_count_total=(1 if int(row["chapters"] or 1) == 1 else None),
                    fandoms=["Harry Potter - J. K. Rowling"],
                    characters=list(row["main_characters"] or []),
                    relationships=list(row["ships"] or []),
                    tags=([f"era: {row['era']}"] if row.get("era") else []) +
                         [f"spoilers: {s}" for s in (row["spoilers"] or []) if s],
                    warnings=[],
                    categories=[],
                    genres=list(row["genres"] or []),
                    is_crossover=False,
                    hits=int(row["hits"] or 0),
                    is_hosted=with_chapters,
                    wayback_url=wayback_url_for(sid, aid),
                    published_at=row["published"] if isinstance(row["published"], datetime) else None,
                    updated_at=row["updated"] if isinstance(row["updated"], datetime) else row["published"],
                )
                db.add(story)
                db.flush()  # get the id
                existing[url] = story.id

                if with_chapters:
                    # Fetch chapters for this story
                    ch_cur = src.cursor(cursor_factory=RealDictCursor)
                    ch_cur.execute("""
                        SELECT chapter, title, summary, content, posted, words,
                               start_author_note, end_author_note
                        FROM stories_chapters
                        WHERE author_id = %s AND story_id = %s AND is_corrupt = false
                        ORDER BY chapter
                    """, (aid, sid))
                    for ch in ch_cur.fetchall():
                        if not ch["content"]:
                            continue
                        chapter = Chapter(
                            story_id=story.id,
                            number=int(ch["chapter"] or 1),
                            title=(ch["title"] or "").strip() or None,
                            summary=(ch["summary"] or "").strip() or None,
                            content=ch["content"],
                            word_count=int(ch["words"] or 0),
                            posted_at=ch["posted"] if isinstance(ch["posted"], datetime) else None,
                            start_note=(ch["start_author_note"] or "").strip() or None,
                            end_note=(ch["end_author_note"] or "").strip() or None,
                        )
                        db.add(chapter)
                        inserted_chapters += 1
                    ch_cur.close()

                inserted_stories += 1

        if (i + batch_size) % 500 == 0 or (i + batch_size) >= len(rows):
            log.info(f"  Progress: {min(i + batch_size, len(rows))}/{len(rows)} | "
                     f"+{inserted_stories} stories | +{inserted_chapters} chapters")

    cur.close()
    src.close()
    log.info(f"Done. Stories: {inserted_stories:,} | Chapters: {inserted_chapters:,} | Skipped: {skipped_stories:,}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--include-hidden",  action="store_true")
    p.add_argument("--include-corrupt", action="store_true")
    p.add_argument("--no-chapters",     action="store_true",
                   help="Import metadata only, no chapter text")
    args = p.parse_args()

    import_all(args.limit, args.include_hidden, args.include_corrupt,
               with_chapters=not args.no_chapters)


if __name__ == "__main__":
    main()
