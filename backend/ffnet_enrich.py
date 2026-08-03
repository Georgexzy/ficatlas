"""
Backfill FF.net genres, characters and engagement counts from the Wayback Machine.
================================================================================

The HuggingFace FF.net metadata dump this index was built from carries only
source_file, category, rating, chapters, words, story_url, summary and language.
No genres, no characters, no engagement counts — so 6.6M FF.net works have no
content tags at all, and the whole index has almost no popularity signal
(529 works out of 19.8M have kudos).

FF.net itself returns 403 to any server-side request, but archive.org's copies
are fetchable, and an archived story page carries the full metadata line:

    Rated: Fiction M - English - Adventure/Drama - Link, Zelda, Jon S., Tyrion L.
     - Chapters: 4 - Words: 4,433 - Reviews: 5 - Favs: 8 - Follows: 14

which yields genres, characters AND favs/follows/reviews.

This is one HTTP request per story, so it will never cover all 6.6M. It is a
backfill: run it against the stories that matter most (longest first by default,
since those are what people actually read), let it work through them over time,
and re-run whenever. Every row it touches is one that previously had no tags.

Usage
-----
    docker compose exec backend python ffnet_enrich.py --limit 200 --dry-run
    docker compose exec backend python ffnet_enrich.py --limit 5000
    docker compose exec backend python ffnet_enrich.py            # until exhausted
"""

import argparse
import logging
import os
import re
import sys
import time

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

import httpx
from sqlalchemy import text as sql_text

from db.session import db_session
from models.story import Story

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

CDX = "http://web.archive.org/cdx/search/cdx"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# FF.net's fixed genre vocabulary. Used to tell the genre segment apart from the
# character segment, since neither is labelled and both are optional.
GENRES = {
    "Adventure", "Angst", "Crime", "Drama", "Family", "Fantasy", "Friendship",
    "General", "Horror", "Humor", "Hurt/Comfort", "Mystery", "Parody", "Poetry",
    "Romance", "Sci-Fi", "Spiritual", "Supernatural", "Suspense", "Tragedy",
    "Western",
}

LANGUAGES = {
    "English", "Spanish", "French", "German", "Portuguese", "Italian", "Dutch",
    "Polish", "Russian", "Chinese", "Japanese", "Korean", "Indonesian", "Filipino",
    "Finnish", "Swedish", "Norwegian", "Danish", "Hungarian", "Czech", "Romanian",
    "Turkish", "Greek", "Hebrew", "Arabic", "Ukrainian", "Vietnamese", "Thai",
    "Catalan", "Esperanto", "Latin", "Bulgarian", "Croatian", "Serbian",
}

_LABELLED = re.compile(
    r"^(Chapters|Words|Reviews|Favs|Follows|Published|Updated|Status|Rated|id)\s*:", re.I)


def parse_ffn_meta(page_text: str) -> dict | None:
    """Pull the metadata line out of an archived FF.net story page.

    The line is a " - " separated list in which almost everything is optional —
    single-chapter stories omit "Chapters:", old ones omit "Words:" and "Favs:",
    and a story with no characters listed simply has no character segment. A
    single regex expecting a fixed shape matched only 2 of 6 real pages, so each
    segment is classified instead:

      * "Rated: X"                     -> rating
      * a bare word in LANGUAGES       -> language
      * all parts in GENRES            -> genres  ("Adventure/Drama")
      * "Label: value"                 -> counts and dates
      * anything else, before counts   -> characters
    """
    txt = re.sub(r"<[^>]+>", " ", page_text)
    txt = txt.replace("&amp;", "&").replace("&#160;", " ").replace("&nbsp;", " ")
    txt = re.sub(r"\s+", " ", txt)

    i = txt.find("Rated:")
    if i < 0:
        return None
    # The line ends at the story id; fall back to a generous slice.
    end = txt.find("id:", i)
    segment = txt[i: end if 0 < end < i + 600 else i + 400]

    out: dict = {"genres": [], "characters": [], "relationships": []}
    seen_counts = False
    for raw in segment.split(" - "):
        part = raw.strip().rstrip(",")
        if not part:
            continue
        if part.lower().startswith("rated:"):
            out["rating"] = part.split(":", 1)[1].replace("Fiction", "").strip()
            continue
        if part in LANGUAGES:
            out["language"] = part
            continue
        m = _LABELLED.match(part)
        if m:
            key = m.group(1).lower()
            val = part.split(":", 1)[1].strip()
            if key in ("reviews", "favs", "follows", "chapters", "words"):
                seen_counts = True
                try:
                    out[key] = int(val.replace(",", ""))
                except ValueError:
                    pass
            continue
        if part.lower().startswith("complete"):
            out["complete"] = True
            continue
        # Unlabelled: genres if every slash-part is a known genre, else characters.
        pieces = [p.strip() for p in part.split("/") if p.strip()]
        if pieces and all(p in GENRES for p in pieces):
            out["genres"] = pieces
        elif not seen_counts and not out["characters"]:
            # FF.net marks a romantic pairing by bracketing it:
            #   "[Renamon, Terriermon] Aayla S., Lopmon"
            # means Renamon/Terriermon are shipped and the rest just appear. That
            # bracket is the ONLY relationship data FF.net exposes, and these works
            # otherwise have none at all, so it's worth extracting rather than
            # leaving "[Renamon" as a character name.
            for pair in re.findall(r"\[([^\]]+)\]", part):
                members = [c.strip() for c in pair.split(",") if c.strip()]
                if len(members) >= 2:
                    out.setdefault("relationships", []).append("/".join(members))
            # Replace the brackets with commas, not spaces: "] Aayla S." has no
            # comma before it, so a space would glue it onto the previous name.
            plain = re.sub(r"[\[\]]", ",", part)
            # Characters are comma- or &-separated, and quoted nicknames are common.
            chars = [c.strip() for c in re.split(r",|\s&\s", plain) if c.strip()]
            out["characters"] = [c for c in chars if 1 < len(c) <= 60][:8]
    return out


def fetch_meta(client: httpx.Client, site_id: str) -> dict | None:
    """Find an archived copy of a story page and parse its metadata line."""
    try:
        rows = client.get(CDX, params={
            "url": f"fanfiction.net/s/{site_id}/1/*",
            "output": "json", "limit": "1", "filter": "statuscode:200",
        }, timeout=40).json()
    except Exception:
        return None
    if not rows or len(rows) < 2:
        return None
    ts, original = rows[1][1], rows[1][2]
    try:
        page = client.get(f"https://web.archive.org/web/{ts}/{original}",
                          timeout=60, follow_redirects=True)
    except Exception:
        return None
    if page.status_code != 200:
        return None
    return parse_ffn_meta(page.text)


def _pick_targets(limit: int | None) -> list:
    """Choose which stories to enrich, in a transaction that ends immediately.

    Kept deliberately separate from the fetching. Holding one session open across
    the whole run left a transaction idle-in-transaction for minutes while each
    archive.org request ran, and an open transaction on `stories` blocks any
    ACCESS EXCLUSIVE lock — so init_db()'s `ALTER TABLE stories ADD COLUMN IF NOT
    EXISTS` waited behind it on every API start, the lifespan never completed, and
    the whole app returned 500 until the transaction was killed.

    Longest first: those are the stories people actually read.
    """
    with db_session() as db:
        return db.execute(sql_text("""
            SELECT id, site_id, word_count
            FROM stories
            WHERE site = 'ffnet'
              AND (characters IS NULL OR cardinality(characters) = 0)
              AND site_id ~ '^[0-9]+$'
            ORDER BY word_count DESC NULLS LAST
            LIMIT :lim
        """), {"lim": limit or 1000}).fetchall()


def run(limit: int | None, dry_run: bool, delay: float, batch: int) -> None:
    updated = missing = failed = 0
    rows = _pick_targets(limit)
    log.info(f"{len(rows)} FF.net stories to enrich")
    pending: list[tuple] = []          # (story_id, parsed metadata) awaiting a write

    with httpx.Client(headers=UA) as client:
        for n, (sid, site_id, wc) in enumerate(rows, 1):
            meta = fetch_meta(client, site_id)
            if not meta:
                missing += 1
            elif any(meta.get(k) for k in
                     ("genres", "characters", "relationships", "favs", "follows", "reviews")):
                pending.append((sid, meta))
            else:
                failed += 1

            if dry_run and n <= 10 and meta:
                log.info(f"  {site_id}: genres={meta.get('genres')} "
                         f"chars={meta.get('characters')} ships={meta.get('relationships')} "
                         f"favs={meta.get('favs')}")

            # Write in short bursts. The database session is only open for the
            # write itself, never across an archive.org request.
            if not dry_run and len(pending) >= batch:
                updated += _write_batch(pending)
                pending.clear()
                log.info(f"  {n}/{len(rows)} — {updated} enriched, {missing} no snapshot")

            time.sleep(delay)

    if dry_run:
        log.info(f"Dry run — {len(pending)} would be enriched, {missing} had no snapshot.")
        return

    if pending:
        updated += _write_batch(pending)
    log.info(f"DONE — enriched={updated} no_snapshot={missing} unparseable={failed}")


def _write_batch(items: list[tuple]) -> int:
    """Apply a batch of parsed metadata. Opens and closes its own short session."""
    written = 0
    with db_session() as db:
        for sid, meta in items:
            story = db.query(Story).filter(Story.id == sid).first()
            if not story:
                continue
            if meta.get("genres"):
                story.genres = meta["genres"]
                # FF.net genres ARE the content tags for these works — they are
                # what a reader filters by, so surface them as tags too.
                existing = list(story.tags or [])
                for g in meta["genres"]:
                    if g not in existing:
                        existing.append(g)
                story.tags = existing
            if meta.get("characters"):
                story.characters = meta["characters"]
            if meta.get("relationships"):
                story.relationships = meta["relationships"]
            # Engagement: the index has almost none, so this is the only real
            # popularity signal available for ranking.
            if meta.get("favs") is not None:
                story.kudos = meta["favs"]
            if meta.get("follows") is not None:
                story.bookmarks = meta["follows"]
            if meta.get("reviews") is not None:
                story.comments = meta["reviews"]
            written += 1
        db.commit()
    return written


def main():
    ap = argparse.ArgumentParser(description="Backfill FF.net metadata via Wayback")
    ap.add_argument("--limit", type=int, help="How many stories to attempt")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="Seconds between requests — be kind to archive.org")
    ap.add_argument("--batch", type=int, default=25, help="Commit every N stories")
    args = ap.parse_args()
    run(args.limit, args.dry_run, args.delay, args.batch)


if __name__ == "__main__":
    main()
