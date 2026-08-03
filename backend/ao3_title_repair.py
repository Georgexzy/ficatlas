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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

import httpx
from sqlalchemy import text as sql_text

from db.session import db_session
from models.story import Story

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Identify honestly. This walks a lot of work pages, and a contactable UA means
# AO3 can throttle or mail us rather than having to guess what an anonymous
# browser-shaped client is doing. Work pages (/works/12345) are not disallowed by
# their robots.txt — only /works? and /works/search? are — and no global
# Crawl-delay is set, so the pacing here is courtesy, not a stated limit.
UA = {"User-Agent": "FicAtlas/1.0 (personal fanfiction index; +https://github.com/Georgexzy/ficatlas)"}
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


class RateLimiter:
    """Caps how often a request may START, across every worker thread.

    The pacing target is a total rate against AO3, not a per-thread one, so the
    limiter is global and the pool size below it only decides how much waiting
    happens in parallel. Raising WORKERS therefore cannot raise the request
    rate — it can only stop threads idling while AO3 thinks.
    """

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.min_interval
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)


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


# Enough in-flight requests to cover AO3's own latency, and no more.
#
# Measured: a work page takes ~5.6s to come back. Fetching them one at a time
# with a 1s sleep therefore ran at 0.15 req/s, not the intended 1 — the process
# spent 85% of its time blocked on the network, and shortening the sleep would
# not have changed that. WORKERS * (1 / latency) needs to reach the target rate,
# so ~6 covers 1 req/s; 8 leaves headroom for slower pages. The limiter, not
# this number, is what bounds the rate.
WORKERS = int(os.getenv("TITLE_REPAIR_WORKERS", "8"))

# Write in batches rather than a session per row: 300 short transactions per
# pass is pointless churn, and holding one open across the HTTP calls is what
# previously produced an idle-in-transaction session that blocked schema changes
# and took the whole API down with it.
WRITE_BATCH = 50


def _flush(pending: list[tuple[int, str]]) -> None:
    if not pending:
        return
    with db_session() as db:
        for sid, title in pending:
            s = db.query(Story).filter(Story.id == sid).first()
            if s:
                s.title = title[:500]
        db.commit()
    pending.clear()


def run(limit: int, dry_run: bool, delay: float) -> None:
    with db_session() as db:
        rows = db.execute(sql_text(TRUNCATED_SQL), {"lim": limit}).fetchall()
    log.info(f"{len(rows)} truncated-looking AO3 titles to check "
             f"({WORKERS} workers, {delay}s between requests)")

    limiter = RateLimiter(delay)
    counts = {"fixed": 0, "missed": 0, "unchanged": 0}
    pending: list[tuple[int, str]] = []
    lock = threading.Lock()
    started = time.monotonic()

    def handle(row) -> None:
        sid, work_id, stored = row
        limiter.wait()
        real = fetch_title(client, work_id)
        with lock:
            if not real:
                counts["missed"] += 1
            elif len(real) > len(stored or "") and real.lower().startswith((stored or "").lower()):
                counts["fixed"] += 1
                if dry_run:
                    log.info(f"  {work_id}: {stored!r} -> {real!r}")
                else:
                    pending.append((sid, real))
                    if len(pending) >= WRITE_BATCH:
                        _flush(pending)
            else:
                counts["unchanged"] += 1

    # One client for the pool so connections are reused; httpx.Client is thread-safe.
    with httpx.Client(headers=UA, limits=httpx.Limits(max_connections=WORKERS)) as client:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(handle, rows))

    with lock:
        _flush(pending)

    elapsed = max(time.monotonic() - started, 0.001)
    verb = "would repair" if dry_run else "repaired"
    log.info(f"DONE — {verb}={counts['fixed']} unchanged={counts['unchanged']} "
             f"unreachable={counts['missed']} in {elapsed:.0f}s "
             f"({len(rows) / elapsed:.2f} req/s)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair AO3 titles truncated by the dump")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    args = ap.parse_args()
    run(args.limit, args.dry_run, args.delay)


if __name__ == "__main__":
    main()
