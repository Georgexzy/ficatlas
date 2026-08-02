"""
HuggingFace FanFiction.net Metadata Importer
============================================
Imports the mrzjy/fanfiction_meta dataset from HuggingFace — 6.6M FFnet
metadata rows covering FFnet IDs 1 to ~10.9M (roughly 2014-era).

Schema (per row):
    source_file: "{Category} - {Author} - {Title}.txt"  — title & author live here
    category:    "Harry Potter" or "Harry Potter, Naruto" (comma-separated crossovers)
    rating:      K, K+, T, M
    chapters:    string e.g. "4"
    words:       string e.g. "4,433"
    story_url:   FFnet URL e.g. "http://www.fanfiction.net/s/8906943/1/"
    summary:     story summary
    language:    "English", "Spanish", ...

Dataset is stored as arrow; parquet conversion lives at refs/convert/parquet.
This script downloads it via huggingface_hub from inside the container — no curl.

Usage
-----
    sudo docker compose exec backend python huggingface_meta_importer.py \
        --download --fandom "Harry Potter" --limit 100000

    sudo docker compose exec backend python huggingface_meta_importer.py \
        --download --dry-run --limit 100      # peek at what would import
"""

import os
import re
import sys
import argparse
import logging
from typing import Iterator, Optional

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

try:
    import pyarrow.parquet as pq
except ImportError:
    print("ERROR: pyarrow not installed. Add 'pyarrow' to requirements.txt and rebuild backend.")
    sys.exit(1)

from sqlalchemy.exc import IntegrityError
from db.session import db_session
from models.story import Story, SiteEnum, RatingEnum, StatusEnum

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


_RATING_MAP = {
    "K":  RatingEnum.general,
    "K+": RatingEnum.teen,
    "T":  RatingEnum.teen,
    "M":  RatingEnum.mature,
    "MA": RatingEnum.mature,
}


def _normalise_rating(raw) -> RatingEnum:
    if not raw: return RatingEnum.not_rated
    return _RATING_MAP.get(str(raw).strip(), RatingEnum.not_rated)


def _extract_id_from_url(url) -> Optional[str]:
    if not url: return None
    m = re.search(r"fanfiction\.net/s/(\d+)", str(url))
    return m.group(1) if m else None


def _parse_int(raw, default: int = 0) -> int:
    if not raw: return default
    try: return max(0, int(str(raw).replace(",", "")))
    except Exception: return default


def _split_fandoms(raw) -> list:
    if not raw: return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


_SOURCE_FILE_RE = re.compile(r"^(?P<cat>.+?)\s+-\s+(?P<author>.+?)\s+-\s+(?P<title>.+?)\.txt$")

def _parse_source_file(source_file) -> tuple:
    """source_file is '{Category} - {Author} - {Title}.txt'. Return (title, author)."""
    if not source_file: return ("Untitled", "Unknown")
    s = str(source_file).strip()
    m = _SOURCE_FILE_RE.match(s)
    if m:
        return (m.group("title").strip()[:500], m.group("author").strip()[:200])
    s = s[:-4] if s.endswith(".txt") else s
    return (s[:500], "Unknown")


def _ensure_parquet_path(args) -> str:
    if args.file and os.path.isfile(args.file):
        return args.file
    if not args.download:
        log.error("No file found. Pass --file PATH or --download to fetch via HuggingFace.")
        sys.exit(1)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log.error("huggingface_hub not installed. Add it to requirements.txt and rebuild.")
        sys.exit(1)
    log.info("Downloading parquet shards from HuggingFace (refs/convert/parquet branch)…")
    cache_dir = "/app/data/hf_cache"
    os.makedirs(cache_dir, exist_ok=True)
    local_dir = snapshot_download(
        repo_id="mrzjy/fanfiction_meta",
        repo_type="dataset",
        revision="refs/convert/parquet",
        allow_patterns=["default/train/*.parquet"],
        cache_dir=cache_dir,
    )
    for root, _, files in os.walk(local_dir):
        for f in sorted(files):
            if f.endswith(".parquet"):
                log.info(f"Reading from {root}/")
                return os.path.join(root, f)
    log.error("Download completed but no parquet shards found")
    sys.exit(1)


def iter_rows(parquet_path: str) -> Iterator[dict]:
    base_dir = os.path.dirname(parquet_path)
    shards = sorted([os.path.join(base_dir, f) for f in os.listdir(base_dir) if f.endswith(".parquet")])
    if not shards: shards = [parquet_path]
    log.info(f"Reading {len(shards)} parquet shard(s)")
    for shard in shards:
        log.info(f"  → {os.path.basename(shard)}")
        pf = pq.ParquetFile(shard)
        for batch in pf.iter_batches(batch_size=5000):
            d = batch.to_pydict()
            cols = list(d.keys())
            n = len(d[cols[0]])
            for i in range(n):
                yield {k: d[k][i] for k in cols}


def main():
    parser = argparse.ArgumentParser(description="Import HF mrzjy/fanfiction_meta dataset")
    parser.add_argument("--file", help="Path to a parquet file (sibling shards auto-discovered)")
    parser.add_argument("--download", action="store_true", help="Download from HuggingFace (~2GB)")
    parser.add_argument("--fandom", help="Filter rows whose `category` matches (substring, case-insensitive)")
    parser.add_argument("--limit", type=int, help="Max rows to import")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Bump HF download timeout — default 10s times out on ~270MB shards on slower links
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")

    parquet_path = _ensure_parquet_path(args)
    log.info(f"Starting import from {parquet_path}")
    log.info(f"Filters: fandom={args.fandom!r}  limit={args.limit}  dry_run={args.dry_run}")

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy import inspect

    inserted = skipped_dup = skipped_missing = filtered_out = errors = 0
    pending: list[dict] = []  # row dicts (not Story instances) for bulk ON CONFLICT DO NOTHING
    fandom_re = re.compile(re.escape(args.fandom), re.IGNORECASE) if args.fandom else None

    table = Story.__table__

    def flush(db, rows: list[dict]) -> int:
        """Insert rows with ON CONFLICT DO NOTHING; returns number actually inserted."""
        if not rows:
            return 0
        stmt = pg_insert(table).values(rows)
        # Skip on conflict for either of the unique keys (url, or (site, site_id))
        stmt = stmt.on_conflict_do_nothing()
        result = db.execute(stmt)
        db.commit()
        # rowcount is reliable for ON CONFLICT DO NOTHING on Postgres
        return result.rowcount or 0

    with db_session() as db:
        for row in iter_rows(parquet_path):
            if args.limit and (inserted + len(pending)) >= args.limit:
                break

            url = row.get("story_url")
            site_id = _extract_id_from_url(url) if url else None
            if not site_id or not url:
                skipped_missing += 1; continue
            if not str(url).startswith("https"):
                url = str(url).replace("http://", "https://", 1)

            fandoms = _split_fandoms(row.get("category"))
            if fandom_re and not any(fandom_re.search(f) for f in fandoms):
                filtered_out += 1; continue

            title, author = _parse_source_file(row.get("source_file"))
            summary = row.get("summary")
            if summary: summary = str(summary)[:2000]

            try:
                import uuid
                pending.append(dict(
                    id=uuid.uuid4(),
                    site=SiteEnum.ffnet.value, site_id=site_id, url=url,
                    title=(title or "Untitled")[:500],
                    author=(author or "Unknown")[:200],
                    summary=summary,
                    language=str(row.get("language") or "English")[:50],
                    rating=_normalise_rating(row.get("rating")).value,
                    status=StatusEnum.in_progress.value,
                    word_count=_parse_int(row.get("words")),
                    chapter_count=max(1, _parse_int(row.get("chapters"), 1)),
                    fandoms=fandoms,
                    characters=[], relationships=[],
                    tags=["ffnet_dump", "hf_meta_2024"],
                    warnings=[], categories=[], genres=[],
                    is_hosted=False, is_crossover=len(fandoms) > 1,
                ))
            except Exception as e:
                errors += 1
                if errors < 5: log.warning(f"Row error: {e}")
                continue

            if not args.dry_run and len(pending) >= args.batch_size:
                # Dedupe within the batch first — Postgres rejects intra-statement dups too
                seen_urls = set()
                deduped = []
                for r in pending:
                    if r["url"] in seen_urls: continue
                    seen_urls.add(r["url"]); deduped.append(r)
                batch_dups = len(pending) - len(deduped)
                added = flush(db, deduped)
                inserted += added
                skipped_dup += (len(deduped) - added) + batch_dups
                pending = []
                if inserted and inserted % 10000 == 0:
                    log.info(f"  {inserted:,} inserted, {skipped_dup:,} dups, {filtered_out:,} filtered")

        if pending and not args.dry_run:
            seen_urls = set()
            deduped = []
            for r in pending:
                if r["url"] in seen_urls: continue
                seen_urls.add(r["url"]); deduped.append(r)
            batch_dups = len(pending) - len(deduped)
            added = flush(db, deduped)
            inserted += added
            skipped_dup += (len(deduped) - added) + batch_dups

        if args.dry_run:
            inserted = len(pending)

    log.info(f"DONE — inserted={inserted:,}  dup_skipped={skipped_dup:,}  "
             f"filtered_out={filtered_out:,}  missing_id={skipped_missing:,}  errors={errors:,}")

    # Refresh planner statistics. Without this the planner keeps using the row
    # counts from before the import and search slows down by orders of magnitude.
    if not args.dry_run:
        from sqlalchemy import text
        try:
            with db_session() as db:
                db.execute(text("ANALYZE stories"))
            log.info("ANALYZE stories — planner statistics refreshed")
        except Exception as e:
            log.warning(f"ANALYZE failed ({e}); run it manually or search will be slow")


if __name__ == "__main__":
    main()
