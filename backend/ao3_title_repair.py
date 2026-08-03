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
from datetime import datetime, timezone
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
    """Caps how often a request may START, across every worker thread, and backs
    off when AO3 says we are going too fast.

    The pacing target is a total rate against AO3, not a per-thread one, so the
    limiter is global and the pool size below it only decides how much waiting
    happens in parallel. Raising WORKERS cannot raise the request rate.

    The interval ADAPTS, because AO3's stated rules turned out not to be the
    real ones. Their robots.txt sets no Crawl-delay for `*` and does not
    disallow work pages, but running at ~0.87 req/s over 8 connections produced
    76 HTTP 429s in a single 300-work pass, against zero for the serial version.
    So there is a real limit that is not written down anywhere, and a fixed
    interval picked by hand is just a guess at it.

    Instead: widen hard on 429 (multiplicatively — the fast direction, because
    being over the limit costs AO3), then recover slowly on sustained success
    (additively, in small steps). That converges just under whatever the limit
    actually is and re-adapts if it changes, without anyone having to know the
    number. Bounded so a bad patch cannot wander into either hammering or
    stalling.
    """

    MIN_INTERVAL = 0.5
    MAX_INTERVAL = 30.0
    BACKOFF = 2.0          # multiply on 429
    RECOVER = 0.98         # shrink per clean request, i.e. ~2% at a time
    RECOVER_AFTER = 25     # consecutive clean requests before easing off

    def __init__(self, min_interval: float):
        self.interval = min_interval
        self.base = min_interval
        self._lock = threading.Lock()
        self._next = 0.0
        self._clean = 0
        self.throttled = 0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def penalise(self, retry_after: float | None = None) -> None:
        """Called on a 429. Widens the interval and parks the queue."""
        with self._lock:
            self.throttled += 1
            self._clean = 0
            self.interval = min(self.interval * self.BACKOFF, self.MAX_INTERVAL)
            # Honour Retry-After by pushing the whole queue out, not just this
            # thread — every worker shares the limit AO3 is complaining about.
            pause = retry_after if retry_after is not None else self.interval
            self._next = max(self._next, time.monotonic() + pause)

    def reward(self) -> None:
        """Called on a clean response. Eases back toward the target rate."""
        with self._lock:
            self._clean += 1
            if self._clean >= self.RECOVER_AFTER and self.interval > self.base:
                self._clean = 0
                self.interval = max(self.interval * self.RECOVER,
                                    self.base, self.MIN_INTERVAL)


# Sentinel meaning "AO3 refused, ask again later" — distinct from None, which
# means the work is genuinely gone or locked. Conflating the two counted every
# throttled request as an unreachable work and dropped it from the queue for
# good, so a rate limit quietly looked like 154 deleted works.
THROTTLED = object()


def fetch_title(client: httpx.Client, work_id: str, limiter: "RateLimiter"):
    try:
        r = client.get(f"https://archiveofourown.org/works/{work_id}",
                       timeout=45, follow_redirects=True)
    except Exception:
        return None
    if r.status_code == 429:
        try:
            retry_after = float(r.headers.get("retry-after", "") or 0) or None
        except ValueError:
            retry_after = None
        limiter.penalise(retry_after)
        return THROTTLED
    limiter.reward()
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
WORKERS = int(os.getenv("TITLE_REPAIR_WORKERS", "4"))

# Write in batches rather than a session per row: 300 short transactions per
# pass is pointless churn, and holding one open across the HTTP calls is what
# previously produced an idle-in-transaction session that blocked schema changes
# and took the whole API down with it.
WRITE_BATCH = 50


def _flush(pending: list[tuple[int, str]], touched: list[int]) -> None:
    """Write repaired titles, and stamp crawled_at on everything we looked at.

    The stamp is what keeps the queue moving. TRUNCATED_SQL orders by
    crawled_at ASC, so a work that cannot be fetched — deleted, orphaned, or
    locked to registered users — would otherwise sit at the head of the queue
    and be re-requested on every single pass, forever. With 154 of 300 rows
    unreachable in one measured pass, the backlog would have converged to a set
    of dead works being asked for indefinitely while real ones never came up.

    Stamping moves them to the back instead. They are still retried eventually,
    which is correct — works do come back from being locked — just not ahead of
    rows nobody has checked yet.
    """
    if not pending and not touched:
        return
    fixes = dict(pending)
    with db_session() as db:
        for sid in touched:
            s = db.query(Story).filter(Story.id == sid).first()
            if not s:
                continue
            if sid in fixes:
                s.title = fixes[sid][:500]
            s.crawled_at = datetime.now(timezone.utc)
        db.commit()
    pending.clear()
    touched.clear()


def run(limit: int, dry_run: bool, delay: float) -> None:
    with db_session() as db:
        rows = db.execute(sql_text(TRUNCATED_SQL), {"lim": limit}).fetchall()
    log.info(f"{len(rows)} truncated-looking AO3 titles to check "
             f"({WORKERS} workers, {delay}s between requests)")

    limiter = RateLimiter(delay)
    counts = {"fixed": 0, "missed": 0, "unchanged": 0, "throttled": 0}
    pending: list[tuple[int, str]] = []
    touched: list[int] = []
    lock = threading.Lock()
    started = time.monotonic()

    def handle(row) -> None:
        sid, work_id, stored = row
        limiter.wait()
        real = fetch_title(client, work_id, limiter)
        # One retry after a throttle, since the limiter has now widened and
        # parked the queue. Still throttled after that: leave the row alone so
        # the next pass picks it up rather than burning it as unreachable.
        if real is THROTTLED:
            limiter.wait()
            real = fetch_title(client, work_id, limiter)

        with lock:
            if real is THROTTLED:
                # Not our answer to record — do not stamp, so it keeps its place
                # in the queue rather than being pushed to the back unexamined.
                counts["throttled"] += 1
                return

            if not real:
                counts["missed"] += 1
            elif len(real) > len(stored or "") and real.lower().startswith((stored or "").lower()):
                counts["fixed"] += 1
                if dry_run:
                    log.info(f"  {work_id}: {stored!r} -> {real!r}")
                else:
                    pending.append((sid, real))
            else:
                counts["unchanged"] += 1

            # A dry run must not write anything at all, stamps included.
            if not dry_run:
                touched.append(sid)
                if len(touched) >= WRITE_BATCH:
                    _flush(pending, touched)

    # One client for the pool so connections are reused; httpx.Client is thread-safe.
    with httpx.Client(headers=UA, limits=httpx.Limits(max_connections=WORKERS)) as client:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(handle, rows))

    with lock:
        _flush(pending, touched)

    elapsed = max(time.monotonic() - started, 0.001)
    verb = "would repair" if dry_run else "repaired"
    log.info(f"DONE — {verb}={counts['fixed']} unchanged={counts['unchanged']} "
             f"unreachable={counts['missed']} throttled={counts['throttled']} "
             f"in {elapsed:.0f}s ({len(rows) / elapsed:.2f} req/s, "
             f"{limiter.throttled} x 429, interval now {limiter.interval:.2f}s)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair AO3 titles truncated by the dump")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=2.0, help="Target seconds between requests")
    args = ap.parse_args()
    run(args.limit, args.dry_run, args.delay)


if __name__ == "__main__":
    main()
