"""
AO3 metadata importer — trentmkelly/archiveofourown-meta
========================================================

A ~7.4GB ungated JSONL dump of Archive of Our Own work metadata, one JSON object
per line, keyed by AO3 work ID:

    {"id": "16", "title": "Proximity", "metadata": {
        "Fandom": "American Idol RPF (Season 7)",
        "Characters": "David Archuleta, David Cook",
        "Relationship": "Cook/Archuleta",
        "Additional Tags": "Kissing, commentfic",
        "Archive Warning": "Creator Chose Not To Use Archive Warnings",
        "Category": "M/M", "Rating": "Teen And Up Audiences",
        "Language": "English", "author": "by astolat",
        "chapters": "1/1", "words": "2,482", "published": "2008-09-13"}}

Why this dataset matters here: the index had only ~3k AO3 works, and 99.7% of all
indexed stories had no relationship data and 98.8% no character data, which is
what made the ship and character filters useless. This dump carries exactly those
fields, and the work ID gives a real, clickable archiveofourown.org URL.

Streams and inserts in batches with ON CONFLICT DO NOTHING, so it is safe to
interrupt and re-run — it will skip what is already indexed.

Usage
-----
    docker compose exec backend python ao3_meta_importer.py --download --limit 200 --dry-run
    docker compose exec backend python ao3_meta_importer.py --download
    docker compose exec backend python ao3_meta_importer.py --download --skip 4000000
"""

import os
import sys
import json
import argparse
import logging

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.session import db_session
from models.story import Story, SiteEnum, RatingEnum, StatusEnum

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DATA_URL = ("https://huggingface.co/datasets/trentmkelly/archiveofourown-meta/"
            "resolve/main/combined_metadata.jsonl")
DEFAULT_PATH = "/app/data/ao3_meta/combined_metadata.jsonl"
PROVENANCE_TAG = "ao3_meta_dump"

_RATING_MAP = {
    "general audiences":     RatingEnum.general,
    "teen and up audiences": RatingEnum.teen,
    "mature":                RatingEnum.mature,
    "explicit":              RatingEnum.explicit,
    "not rated":             RatingEnum.not_rated,
}

# AO3 exports these as comma-joined strings. Keys vary between singular and plural
# across rows in this dump, so every field is looked up under both spellings.
_FIELD_KEYS = {
    "fandoms":       ("Fandoms", "Fandom"),
    "characters":    ("Characters", "Character"),
    "relationships": ("Relationships", "Relationship"),
    "tags":          ("Additional Tags", "Additional Tag"),
    "warnings":      ("Archive Warnings", "Archive Warning"),
    "categories":    ("Categories", "Category"),
}


def _split_multi(value) -> list[str]:
    """AO3 joins multi-valued tags with ", ". Split on that rather than a bare
    comma, so names that legitimately contain one ("Rogers, Steve") survive."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(", ") if p.strip()]


def _get(meta: dict, field: str) -> list[str]:
    for key in _FIELD_KEYS[field]:
        if meta.get(key):
            return _split_multi(meta[key])
    return []


def _parse_int(value, default=0) -> int:
    if value is None:
        return default
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, AttributeError):
        return default


def _parse_chapters(value) -> tuple[int, int | None]:
    """"12/30" -> (12, 30);  "1/?" -> (1, None)."""
    if not value:
        return 1, None
    text = str(value).strip()
    if "/" not in text:
        return max(1, _parse_int(text, 1)), None
    posted, _, total = text.partition("/")
    return max(1, _parse_int(posted, 1)), (None if total.strip() in ("?", "") else _parse_int(total) or None)


def _parse_date(value):
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _to_row(obj: dict) -> dict | None:
    work_id = str(obj.get("id") or "").strip()
    title = (obj.get("title") or "").strip()
    if not work_id or not work_id.isdigit() or not title:
        return None

    meta = obj.get("metadata") or {}

    # The dump stores the byline as AO3 renders it: "by astolat".
    author = str(meta.get("author") or "").strip()
    if author.lower().startswith("by "):
        author = author[3:].strip()

    posted, total = _parse_chapters(meta.get("chapters"))
    # A work is complete when it says so, or when every chapter is posted.
    completed = bool(str(meta.get("completed") or "").strip())
    status = (StatusEnum.complete
              if completed or (total is not None and posted >= total)
              else StatusEnum.in_progress)

    rating = _RATING_MAP.get(str(meta.get("Rating") or "").strip().lower(),
                             RatingEnum.not_rated)

    fandoms = _get(meta, "fandoms")
    tags = _get(meta, "tags")

    import uuid
    return dict(
        id=uuid.uuid4(),
        site=SiteEnum.ao3.value,
        site_id=work_id[:64],
        url=f"https://archiveofourown.org/works/{work_id}",
        title=title[:500],
        author=(author or "Anonymous")[:200],
        author_url=None,
        summary=(obj.get("summary") or meta.get("Summary") or None),
        language=str(meta.get("Language") or "English")[:32],
        rating=rating.value,
        status=status.value,
        word_count=_parse_int(meta.get("words")),
        chapter_count=posted,
        chapter_count_total=total,
        fandoms=fandoms,
        characters=_get(meta, "characters"),
        relationships=_get(meta, "relationships"),
        tags=[*tags, PROVENANCE_TAG],
        warnings=_get(meta, "warnings"),
        categories=_get(meta, "categories"),
        genres=[],
        published_at=_parse_date(meta.get("published")),
        is_hosted=False,
        is_crossover=len(fandoms) > 1,
    )


def download(path: str) -> str:
    """Stream the dump to disk, resuming a partial file with a Range request."""
    import httpx
    os.makedirs(os.path.dirname(path), exist_ok=True)
    have = os.path.getsize(path) if os.path.exists(path) else 0

    with httpx.Client(follow_redirects=True, timeout=120) as client:
        total = _parse_int(client.head(DATA_URL).headers.get("content-length"))
        if have and total and have >= total:
            log.info(f"Already downloaded ({have:,} bytes)")
            return path

        headers = {"Range": f"bytes={have}-"} if have else {}
        if have:
            log.info(f"Resuming at {have:,} / {total:,} bytes")
        with client.stream("GET", DATA_URL, headers=headers) as r:
            r.raise_for_status()
            with open(path, "ab" if have else "wb") as fh:
                seen = have
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    seen += len(chunk)
                    if total and seen % (200 << 20) < (1 << 20):
                        log.info(f"  {seen / 1e9:.1f} / {total / 1e9:.1f} GB")
    log.info(f"Downloaded to {path}")
    return path


def run(path: str, limit: int | None, skip: int, batch_size: int, dry_run: bool):
    table = Story.__table__

    def flush(db, rows: list[dict]) -> int:
        if not rows:
            return 0
        # Dedupe within the batch — Postgres rejects duplicates inside a single
        # statement even with ON CONFLICT DO NOTHING.
        seen, deduped = set(), []
        for r in rows:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            deduped.append(r)
        result = db.execute(pg_insert(table).values(deduped).on_conflict_do_nothing())
        db.commit()
        return result.rowcount or 0

    inserted = dup = bad = 0
    pending: list[dict] = []

    with open(path, "r", encoding="utf-8", errors="replace") as fh, db_session() as db:
        for lineno, line in enumerate(fh):
            if lineno < skip:
                continue
            if limit and inserted + len(pending) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                row = _to_row(json.loads(line))
            except (json.JSONDecodeError, ValueError, TypeError):
                bad += 1
                continue
            if row is None:
                bad += 1
                continue

            if dry_run:
                pending.append(row)
                if len(pending) >= (limit or 10):
                    break
                continue

            pending.append(row)
            if len(pending) >= batch_size:
                added = flush(db, pending)
                inserted += added
                dup += len(pending) - added
                pending = []
                if inserted and inserted % 50000 < batch_size:
                    log.info(f"  line {lineno:,} — {inserted:,} inserted, {dup:,} already indexed")

        if pending and not dry_run:
            added = flush(db, pending)
            inserted += added
            dup += len(pending) - added

    if dry_run:
        for r in pending[: (limit or 10)]:
            log.info(f"  {r['title'][:44]!r} by {r['author'][:22]!r}")
            log.info(f"      fandoms={r['fandoms'][:2]} ships={r['relationships'][:2]} "
                     f"chars={r['characters'][:2]} words={r['word_count']}")
        log.info(f"Dry run — parsed {len(pending)} rows, nothing written.")
        return

    log.info(f"DONE — inserted={inserted:,}  already_indexed={dup:,}  unparseable={bad:,}")


def main():
    ap = argparse.ArgumentParser(description="Import AO3 metadata dump")
    ap.add_argument("--download", action="store_true", help="Fetch the dump (~7.4GB, resumable)")
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip", type=int, default=0, help="Skip N leading lines (resume)")
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = args.path
    if args.download:
        path = download(path)
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found — pass --download to fetch it first.")

    run(path, args.limit, args.skip, args.batch_size, args.dry_run)


if __name__ == "__main__":
    main()
