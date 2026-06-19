"""
janelleshane/harry-potter-fanfic-dataset importer
==================================================
Imports 111,963 Harry Potter fanfic titles, authors and summaries scraped (with
permission) from AO3 by @b8horpet, published in the janelleshane GitHub repo.

This is a METADATA-ONLY seed: no word counts, ratings, ships or full text — just
title/author/summary. It's useful as a broad discovery layer that the richer
sources (HF dump, FicHub imports, AO3 live) then fill in. Rows are tagged
`janelleshane_seed` for provenance and are matched against existing works by the
cross-post detector so we don't create duplicates of stories we already have.

The data file in the repo is a flat text listing. We parse defensively because
the format is loose: blocks separated by blank lines, with a title line, a
"by {author}" line, and summary text.

Network: raw.githubusercontent.com is on the container allowlist, so we stream
the file directly with httpx — no git clone needed.

Usage
-----
    sudo docker compose exec backend python janelleshane_importer.py --download
    sudo docker compose exec backend python janelleshane_importer.py --download --limit 500 --dry-run
"""
import argparse
import re
import sys

RAW_URL = (
    "https://raw.githubusercontent.com/janelleshane/"
    "harry-potter-fanfic-dataset/master/hpac_only_fics.txt"
)
# Fallback filenames seen in the repo over time
ALT_URLS = [
    "https://raw.githubusercontent.com/janelleshane/harry-potter-fanfic-dataset/master/hp_fics.txt",
    "https://raw.githubusercontent.com/janelleshane/harry-potter-fanfic-dataset/main/hpac_only_fics.txt",
]

PROVENANCE_TAG = "janelleshane_seed"


def _fetch_text() -> str:
    import httpx
    urls = [RAW_URL, *ALT_URLS]
    last_err = None
    for u in urls:
        try:
            r = httpx.get(u, timeout=60, follow_redirects=True)
            if r.status_code == 200 and len(r.text) > 1000:
                print(f"Downloaded {len(r.text):,} bytes from {u}")
                return r.text
            last_err = f"HTTP {r.status_code} from {u}"
        except Exception as e:
            last_err = f"{e} ({u})"
    raise SystemExit(f"Could not download dataset. Last error: {last_err}")


# Each fic block tends to look like:
#   Title Of The Fic by AuthorName
#   Summary text, possibly multiple lines...
# separated by blank lines. We split on blank lines and parse the first line
# for "title by author".
_BY_RE = re.compile(r"^(?P<title>.+?)\s+by\s+(?P<author>.+?)\s*$", re.IGNORECASE)


def _parse_blocks(text: str):
    blocks = re.split(r"\n\s*\n", text)
    for blk in blocks:
        lines = [l.strip() for l in blk.splitlines() if l.strip()]
        if not lines:
            continue
        m = _BY_RE.match(lines[0])
        if m:
            title = m.group("title").strip()
            author = m.group("author").strip()
            summary = " ".join(lines[1:]).strip()
        else:
            # No "by" — treat first line as title, rest as summary, author unknown
            title = lines[0]
            author = ""
            summary = " ".join(lines[1:]).strip()
        if len(title) < 2:
            continue
        yield {"title": title[:500], "author": (author or "Unknown")[:200],
               "summary": summary[:4000]}


def run(download: bool, limit: int | None, dry_run: bool):
    if not download:
        print("Pass --download to fetch and import.")
        return
    text = _fetch_text()
    rows = list(_parse_blocks(text))
    if limit:
        rows = rows[:limit]
    print(f"Parsed {len(rows):,} fic records.")

    if dry_run:
        for r in rows[:10]:
            print(f"  - {r['title']!r} by {r['author']!r} :: {r['summary'][:60]}…")
        print("Dry run — nothing written.")
        return

    # Import via the same persistence path so cross-post dedup applies.
    sys.path.insert(0, ".")
    from db.session import db_session
    from models.story import Story, SiteEnum, RatingEnum, StatusEnum
    from live_fetch.crosspost import find_crosspost_for

    saved = 0
    attached = 0
    skipped = 0
    with db_session() as db:
        for i, r in enumerate(rows):
            try:
                # If we already have this work (any site), just tag it — no dup row.
                existing = find_crosspost_for(db, r["title"], r["author"])
                if existing is not None:
                    tags = set(existing.tags or [])
                    if PROVENANCE_TAG not in tags:
                        existing.tags = sorted(tags | {PROVENANCE_TAG})
                        db.commit()
                    attached += 1
                    continue

                # Synthetic stable URL for this metadata-only seed (no real page).
                slug = re.sub(r"[^\w]+", "-", f"{r['title']}-{r['author']}".lower()).strip("-")[:120]
                synthetic_url = f"seed://janelleshane/{slug}"
                if db.query(Story.id).filter(Story.url == synthetic_url).first():
                    skipped += 1
                    continue

                story = Story(
                    site=SiteEnum.ao3,           # provenance is AO3-scraped
                    site_id=f"seed_{slug}"[:64],
                    url=synthetic_url,
                    title=r["title"],
                    author=r["author"],
                    summary=r["summary"],
                    language="English",
                    rating=RatingEnum.not_rated,
                    status=StatusEnum.unknown,
                    word_count=0,
                    chapter_count=1,
                    fandoms=["Harry Potter"],
                    tags=[PROVENANCE_TAG],
                    is_hosted=False,
                )
                db.add(story)
                db.commit()
                saved += 1
            except Exception as e:
                db.rollback()
                skipped += 1
                if i < 5:
                    print(f"  skip {r.get('title')!r}: {e}")
            if i % 5000 == 0 and i:
                print(f"  …{i:,}/{len(rows):,} (saved {saved}, attached {attached})")

    print(f"Done. New seed rows: {saved}, attached-to-existing: {attached}, skipped: {skipped}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.download, args.limit, args.dry_run)
