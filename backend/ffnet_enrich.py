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
from datetime import datetime, timezone

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

import httpx
from sqlalchemy import text as sql_text

from db.session import db_session
from models.story import StatusEnum, Story

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


def _ffn_date(value: str):
    """FF.net writes dates two ways, both seen in the wild:

        "Updated: 11/26/2012"   M/D/YYYY
        "Published: 09-19-10"   MM-DD-YY

    Returned as UTC midnight. These were previously parsed and thrown away —
    the docstring below claimed "counts and dates" but only counts were kept,
    which is why 100% of 6,572,195 FF.net rows had no dates at all while the
    line right there on the page carried them.
    """
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _classify_genres(part: str) -> list[str]:
    """Split an unlabelled segment into FF.net genres, or [] if it is not genres.

    Must test the WHOLE string before splitting on "/", because "Hurt/Comfort"
    is itself a single genre. Splitting first turned it into ["Hurt","Comfort"],
    matched neither, and fell through to the character branch — which then also
    blocked the real character list, since that branch only fires when no
    characters have been recorded yet.
    """
    part = part.strip()
    if part in GENRES:
        return [part]
    pieces = [p.strip() for p in part.split("/") if p.strip()]
    if pieces and all(p in GENRES for p in pieces):
        return pieces
    # "Hurt/Comfort/Romance" — recombine adjacent pieces that form a real genre.
    out: list[str] = []
    i = 0
    while i < len(pieces):
        if i + 1 < len(pieces) and f"{pieces[i]}/{pieces[i+1]}" in GENRES:
            out.append(f"{pieces[i]}/{pieces[i+1]}")
            i += 2
        elif pieces[i] in GENRES:
            out.append(pieces[i])
            i += 1
        else:
            return []
    return out


# Off by default is wrong here — the misses are pure waste — but keep the switch
# so it can be turned off if FicHub ever asks.
_FICHUB_FALLBACK = os.getenv("FFNET_FICHUB_FALLBACK", "true").lower() in ("1", "true", "yes")
_fichub_client = None


def _fichub_fallback(site_id: str) -> dict | None:
    """Ask FicHub for a work archive.org never captured, in ffnet_enrich's shape."""
    global _fichub_client
    try:
        import httpx
        from fichub_meta import fetch_meta as fh_meta, HEADERS as FH_HEADERS
        if _fichub_client is None:
            _fichub_client = httpx.Client(headers=FH_HEADERS, follow_redirects=True)
        d = fh_meta(_fichub_client, f"https://www.fanfiction.net/s/{site_id}/1/")
    except Exception:
        return None
    if not d:
        return None
    # Translate to the keys _write_batch already understands.
    return {
        "genres": d.get("genres") or [],
        "characters": d.get("characters") or [],
        "relationships": [],
        "favs": d.get("kudos"),
        "follows": d.get("bookmarks"),
        "reviews": d.get("comments"),
        "words": d.get("word_count"),
        "chapters": d.get("chapter_count"),
        "language": d.get("language"),
        "published_at": d.get("published_at"),
        "updated_at": d.get("updated_at"),
        "complete": d.get("status") == "complete",
    }


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
            elif key in ("published", "updated"):
                dt = _ffn_date(val)
                if dt:
                    out[f"{key}_at"] = dt
            elif key == "status":
                # "Status: Complete". The bare-word test below only catches a
                # segment that STARTS with "complete", so a labelled status was
                # swallowed by this branch and dropped.
                if val.strip().lower().startswith("complete"):
                    out["complete"] = True
            continue
        if part.lower().startswith("complete"):
            out["complete"] = True
            continue
        # Unlabelled: genres if the whole segment resolves to known genres,
        # else characters.
        genres = _classify_genres(part)
        if genres:
            out["genres"] = genres
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
    """Find an archived copy of a story page and parse its metadata line.

    Paced by wayback_harvest.BUDGET rather than by this module's own --delay.
    Two loops now talk to archive.org — this one and the AO3 snapshot harvest —
    and each was pacing itself in isolation. That does not compose: individually
    polite, together they drew a steady stream of dropped connections. It is the
    same failure ao3_budget was written for, so it gets the same fix, including
    carrying 429/503 responses back so a refusal to EITHER loop slows both.
    """
    from wayback_harvest import BUDGET, note_response

    BUDGET.wait()
    try:
        resp = client.get(CDX, params={
            "url": f"fanfiction.net/s/{site_id}/1/*",
            "output": "json", "limit": "1", "filter": "statuscode:200",
        }, timeout=40)
        note_response(resp.status_code)
        rows = resp.json()
    except Exception:
        BUDGET.network_error()
        return None
    if not rows or len(rows) < 2:
        return None
    ts, original = rows[1][1], rows[1][2]
    BUDGET.wait()
    try:
        page = client.get(f"https://web.archive.org/web/{ts}/{original}",
                          timeout=60, follow_redirects=True)
    except Exception:
        BUDGET.network_error()
        return None
    note_response(page.status_code)
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

    Targets are chosen by how much is missing AND how likely the work is to be
    seen, via gap_filler — not just "longest first" as before. Length is a crude
    stand-in for readership and says nothing about how much a row actually
    lacks, so a 200k-word story missing only its language outranked a widely-read
    one with no summary, characters or dates at all.

    Rows already checked and still empty fall to the back through the
    crawled_at tiebreak, so the queue keeps advancing.
    """
    from gap_filler import find_gaps
    with db_session() as db:
        rows = find_gaps(db, "ffnet", limit=limit or 1000)
    # Shape kept as (id, site_id, word_count) for the caller.
    return [(r["id"], r["site_id"], r["gap_score"]) for r in rows]


def run(limit: int | None, dry_run: bool, delay: float, batch: int) -> None:
    updated = missing = failed = from_fichub = 0
    rows = _pick_targets(limit)
    log.info(f"{len(rows)} FF.net stories to enrich")
    pending: list[tuple] = []          # (story_id, parsed metadata) awaiting a write

    with httpx.Client(headers=UA) as client:
        for n, (sid, site_id, wc) in enumerate(rows, 1):
            meta = fetch_meta(client, site_id)
            if not meta and _FICHUB_FALLBACK:
                # ~31% of works have no usable Wayback capture (no_snapshot=62
                # of 200 in a measured pass), and those requests were simply
                # spent for nothing. FicHub resolves FFN URLs directly — it does
                # the Cloudflare work we cannot — so it recovers exactly the
                # ones archive.org never captured.
                #
                # A fallback rather than the primary source on purpose: bulk
                # sweeps of millions of rows belong on the Internet Archive,
                # which is built for that, not on one volunteer's server. This
                # only fires where Wayback has already failed.
                fh = _fichub_fallback(site_id)
                if fh:
                    meta = fh
                    from_fichub += 1
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

            # Pacing lives in the shared archive.org budget now (see
            # fetch_meta), which already blocked for the right interval before
            # each request. Sleeping again here would stack a second delay on
            # top of it and slow the run for no additional politeness. Kept
            # honoured only if a caller asks for extra spacing explicitly.
            if delay:
                time.sleep(delay)

    if dry_run:
        log.info(f"Dry run — {len(pending)} would be enriched, {missing} had no snapshot.")
        return

    if pending:
        updated += _write_batch(pending)
    log.info(f"DONE — enriched={updated} no_snapshot={missing} "
             f"via_fichub={from_fichub} unparseable={failed}")


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

            # Dates. The FFN dump carried none at all, so before this every one
            # of 6,572,195 rows sorted and filtered as undated.
            if meta.get("published_at") and story.published_at is None:
                story.published_at = meta["published_at"]
            upd = meta.get("updated_at")
            if upd and (story.updated_at is None or upd > story.updated_at):
                story.updated_at = upd

            # "Complete" on the page IS evidence, unlike the dump's silence that
            # everything else defaults to `unknown` for.
            if meta.get("complete") and story.status != StatusEnum.complete:
                story.status = StatusEnum.complete

            # Parsed already and previously discarded; only fills gaps.
            if meta.get("words") and not (story.word_count or 0):
                story.word_count = meta["words"]
            if meta.get("chapters") and (story.chapter_count or 0) < meta["chapters"]:
                story.chapter_count = meta["chapters"]

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
