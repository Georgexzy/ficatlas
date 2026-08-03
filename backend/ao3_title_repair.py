"""
Repair AO3 titles truncated by the metadata dump.
=================================================

The bulk AO3 metadata dump ships titles cut off mid-phrase. Verified against AO3
itself:

    dump "Harry Potter and"   ->  "Harry Potter and Homosexual Rights Feat. Severus Snape"
    dump "The Masochism of"   ->  "The Masochism of Self-Defence"
    dump "See Me Now A Ray of" -> (longer)

AO3 rows ending on a dangling "and / of / the / with" are the ones that can be
identified with confidence — no real title ends there. Cuts that happen to land
on a content word are indistinguishable from a short title and are only repaired
if the work is re-encountered by a live fetch.

The work page carries the real title in <h2 class="title heading">, so this walks
the detectable ones and repairs them. One request per work, so it is a slow
backfill rather than a migration; it is idempotent and safe to interrupt.

A fetched title is only accepted when it EXTENDS what we hold — the stored value
must be a prefix of it — so a redirect, an error page or an unrelated work can
never overwrite a good title.

Usage
-----
    docker compose exec backend python ao3_title_repair.py --limit 50 --dry-run
    docker compose exec backend python ao3_title_repair.py --limit 2000
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

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
TITLE_RE = re.compile(r'<h2 class="title heading">(.*?)</h2>', re.S)

# Only endings that genuinely cannot finish a title.
#
# A broader list (for/in/to/by/on/at/a) is mostly false positives — "A Kiss Worth
# Marrying For", "The Boy They Lied To" and "Locked In" are complete titles, and
# checking each costs a request to AO3 for nothing. Measured on this index:
# 398,817 end on and/of/the/with, while 310,660 end on the ambiguous ones.
#
# Restricted to rows that came from the dump, since that is what truncated them;
# titles from live fetches are already correct.
TRUNCATED_SQL = r"""
    SELECT id, site_id, title
    FROM stories
    WHERE site = 'ao3'
      AND site_id ~ '^[0-9]+$'
      AND tags @> ARRAY['ao3_meta_dump']
      AND title ~* ' (and|of|the|with)$'
    ORDER BY crawled_at ASC NULLS FIRST
    LIMIT :lim
"""


def fetch_title(client: httpx.Client, work_id: str) -> str | None:
    try:
        r = client.get(f"https://archiveofourown.org/works/{work_id}",
                       timeout=45, follow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    m = TITLE_RE.search(r.text)
    if not m:
        return None
    title = re.sub(r"<[^>]+>", "", m.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    return title or None


def run(limit: int, dry_run: bool, delay: float) -> None:
    with db_session() as db:
        rows = db.execute(sql_text(TRUNCATED_SQL), {"lim": limit}).fetchall()
    log.info(f"{len(rows)} truncated-looking AO3 titles to check")

    fixed = missed = unchanged = 0
    with httpx.Client(headers=UA) as client:
        for sid, work_id, stored in rows:
            real = fetch_title(client, work_id)
            if not real:
                missed += 1
            elif len(real) > len(stored or "") and real.lower().startswith((stored or "").lower()):
                if dry_run:
                    log.info(f"  {work_id}: {stored!r} -> {real!r}")
                else:
                    with db_session() as db:
                        s = db.query(Story).filter(Story.id == sid).first()
                        if s:
                            s.title = real[:500]
                            db.commit()
                fixed += 1
            else:
                unchanged += 1
            time.sleep(delay)

    verb = "would repair" if dry_run else "repaired"
    log.info(f"DONE — {verb}={fixed} unchanged={unchanged} unreachable={missed}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair AO3 titles truncated by the dump")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    args = ap.parse_args()
    run(args.limit, args.dry_run, args.delay)


if __name__ == "__main__":
    main()
