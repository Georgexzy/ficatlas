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

One request per work, so it is a slow backfill rather than a migration; it is
idempotent and safe to interrupt.

A fetched title is only accepted when it EXTENDS what we hold — the stored value
must be a prefix of it — so a redirect, an error page or an unrelated work can
never overwrite a good title. Every other field is gap-filling only: we never
replace something we already have with something we just read, except the
engagement counters, which only ever move up.

Usage
-----
    docker compose exec backend python ao3_title_repair.py --limit 50 --dry-run
    docker compose exec backend python ao3_title_repair.py --limit 2000
"""

import argparse
import html
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
from models.story import StatusEnum, Story

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Identify honestly. This walks a lot of work pages, and a contactable UA means
# AO3 can throttle or mail us rather than having to guess what an anonymous
# browser-shaped client is doing. Work pages (/works/12345) are not disallowed by
# their robots.txt — only /works? and /works/search? are — and no global
# Crawl-delay is set, so the pacing here is courtesy, not a stated limit.
UA = {"User-Agent": "FicAtlas/1.0 (personal fanfiction index; +https://github.com/Georgexzy/ficatlas)"}
TITLE_RE = re.compile(r'<h2 class="title heading">(.*?)</h2>', re.S)

# The stats block, verified against a live page:
#   <dt class="published">Published:</dt><dd class="published">2026-05-23</dd>
#   <dt class="status">Updated:</dt><dd class="status">2026-06-11</dd>
#   <dd class="words">10,014</dd>  <dd class="chapters">4/?</dd>
#   <dd class="comments">31</dd>   <dd class="kudos">19</dd>
#   <dd class="bookmarks"><a href="...">2</a></dd>   <dd class="hits">527</dd>
SUMMARY_RE = re.compile(
    r'<div class="summary module"[^>]*>.*?<blockquote class="userstuff">(.*?)</blockquote>', re.S)
PUBLISHED_RE = re.compile(r'<dd class="published">\s*([\d-]+)\s*</dd>')
# The dt label distinguishes an ongoing work from a finished one; the dd holds
# the date either way, so both are read from the same pair.
STATUS_RE = re.compile(
    r'<dt class="status">\s*([^<:]+):?\s*</dt>\s*<dd class="status">\s*([\d-]+)\s*</dd>')
WORDS_RE = re.compile(r'<dd class="words">\s*([\d,]+)\s*</dd>')
CHAPTERS_RE = re.compile(r'<dd class="chapters">\s*(?:<[^>]+>)?\s*(\d+)\s*/\s*([\d?]+)')
LANGUAGE_RE = re.compile(r'<dd class="language"[^>]*>\s*([^<]+?)\s*</dd>')
_COUNT_RES = {
    "kudos":     re.compile(r'<dd class="kudos">\s*(?:<[^>]+>)?\s*([\d,]+)'),
    "hits":      re.compile(r'<dd class="hits">\s*(?:<[^>]+>)?\s*([\d,]+)'),
    "comments":  re.compile(r'<dd class="comments">\s*(?:<[^>]+>)?\s*([\d,]+)'),
    "bookmarks": re.compile(r'<dd class="bookmarks">\s*(?:<[^>]+>)?\s*([\d,]+)'),
}


def _text(fragment: str) -> str:
    """Markup and entities out, readable text in.

    Stripping tags alone is not enough: some summaries are DOUBLE-escaped, so
    the author's "<p>" reaches us as "&lt;p&gt;" and survives tag-stripping to
    be shown to a reader verbatim. Hence strip, unescape, then strip again for
    whatever the unescape turned back into a tag.
    """
    s = re.sub(r"<[^>]+>", "", fragment)
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.replace(",", ""))
    except ValueError:
        return None


def _date(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_work_page(html_text: str) -> dict | None:
    """Everything the work page carries. None when it is not a work page at all
    (a login redirect, an error, a deleted work)."""
    m = TITLE_RE.search(html_text)
    if not m:
        return None
    out: dict = {"title": _text(m.group(1)) or None}

    m = SUMMARY_RE.search(html_text)
    if m:
        # Keep paragraph breaks; the blockquote is user-authored HTML.
        raw = re.sub(r"</p>\s*<p>", "\n\n", m.group(1))
        out["summary"] = _text(raw)[:5000] or None

    m = PUBLISHED_RE.search(html_text)
    if m:
        out["published_at"] = _date(m.group(1))

    m = STATUS_RE.search(html_text)
    if m:
        label = m.group(1).strip().lower()
        out["updated_at"] = _date(m.group(2))
        # "Completed:" is AO3 stating the work is finished. "Updated:" is not
        # evidence either way on its own — the chapter counts below decide.
        if label.startswith("complet"):
            out["status"] = StatusEnum.complete

    m = WORDS_RE.search(html_text)
    if m:
        out["word_count"] = _int(m.group(1))

    m = CHAPTERS_RE.search(html_text)
    if m:
        posted, total = _int(m.group(1)), _int(m.group(2))
        out["chapter_count"] = posted
        out["chapter_count_total"] = total          # None when AO3 shows "?"
        if out.get("status") is None:
            # "4/?" is AO3 explicitly saying more is coming, which is real
            # evidence of a WIP — unlike the bulk dumps, where a missing value
            # meant nothing and had to become `unknown`.
            if total is None:
                out["status"] = StatusEnum.in_progress
            elif posted is not None and posted >= total:
                out["status"] = StatusEnum.complete

    m = LANGUAGE_RE.search(html_text)
    if m:
        out["language"] = _text(m.group(1))[:32] or None

    for key, rx in _COUNT_RES.items():
        m = rx.search(html_text)
        if m:
            out[key] = _int(m.group(1))
    return out

# Ordering, which has to satisfy two things at once.
#
# Popularity first, so the works people actually open get a summary and a real
# title before the long tail does. But popularity ALONE reintroduces a bug this
# module already had: an unreachable work (deleted, orphaned, locked) would sit
# at the top of the queue and be re-requested on every pass forever, and the
# most popular works are exactly the ones that stay there longest.
#
# So the day a row was last checked is the primary key and popularity only
# breaks ties within it. Never-checked rows (NULL) come first, most-popular
# first; anything checked today drops behind everything not checked today.
# Truncating to the day rather than the timestamp is what makes the tie-break
# apply to a large group instead of ordering by microseconds.
#
# The expressions below must match ix_stories_repair_queue EXACTLY, including
# `AT TIME ZONE 'UTC'` — date_trunc on a timestamptz is only STABLE, so it
# cannot be indexed, and a mismatch silently falls back to a full scan:
# 11,816ms versus 57ms measured.
#
# Engagement is 0 for all but 17 of the ~396k rows right now, so word_count
# carries most of the ordering today. That is deliberate: the harvest itself
# writes kudos and hits back, so this ordering sharpens as it runs rather than
# needing a popularity source up front.

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
    ORDER BY date_trunc('day', crawled_at AT TIME ZONE 'UTC') ASC NULLS FIRST,
             (COALESCE(kudos, 0) + COALESCE(hits, 0)) DESC,
             COALESCE(word_count, 0) DESC
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
    BACKOFF = 2.0          # multiply on 429 — fast, because being over costs AO3
    RECOVER = 0.90         # shrink 10% per recovery step
    RECOVER_AFTER = 10     # consecutive clean requests per step

    # Recovery has to be fast enough to actually happen inside one pass.
    # The first version eased off 2% per 25 clean requests, which sounds
    # cautious and is really just broken: climbing back from a single 429
    # (2.0s -> 4.0s) would have taken ~875 clean requests, so one transient
    # throttle in a 300-work pass cost the ENTIRE pass half its throughput and
    # never recovered. Measured: 0.23 req/s after one 429, no better than the
    # serial version it replaced.
    #
    # 10% per 10 clean requests gets back from 4.0s to 2.0s in ~70 requests —
    # slow enough to stay well under the limit, fast enough that one blip does
    # not write off the pass.

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


def fetch_work(client: httpx.Client, work_id: str, limiter: "RateLimiter"):
    try:
        # view_adult skips the "this work could have adult content" interstitial
        # that would otherwise be parsed as a work page with no title.
        r = client.get(f"https://archiveofourown.org/works/{work_id}?view_adult=true",
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
    # A restricted work redirects to /users/login, which is a 200 with no work
    # markup — parse_work_page returns None for it rather than inventing fields.
    return parse_work_page(r.text)


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


def _norm(text: str) -> str:
    """Lowercase, alphanumerics only — for comparing a dump title to a real one."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _extends(old: str, new: str) -> bool:
    """Is `new` the same title as `old`, continued?

    Compared on alphanumerics only, because the dump did not just truncate
    titles, it also dropped punctuation:

        dump  "A Dead Body A Lot of"
        real  "A Dead Body, A Lot of Cash, and a Whole Heap of Crazy"

    A literal prefix test rejects that over the missing comma, so 2 of every 14
    fetched works kept a broken title after paying for the request. Normalising
    keeps the guarantee that matters — the stored title must still be a genuine
    prefix of what we fetched, so an unrelated work or an error page can never
    overwrite a good title — while tolerating punctuation the dump lost.
    """
    n_old, n_new = _norm(old), _norm(new)
    return bool(n_old) and n_new.startswith(n_old)


def apply_work(story: Story, data: dict) -> list[str]:
    """Merge a fetched page onto a stored row. Returns the field names changed.

    Gap-filling only. The dumps are the authority for anything they supplied;
    this fills what they left empty. The two exceptions are stated inline: the
    title, which may be EXTENDED because the dump truncated it, and the
    engagement counters, which are point-in-time and only move up.
    """
    changed: list[str] = []

    new_title = (data.get("title") or "").strip()
    old_title = (story.title or "").strip()
    if new_title and len(new_title) > len(old_title) and _extends(old_title, new_title):
        story.title = new_title[:500]
        changed.append("title")

    if data.get("summary") and not (story.summary or "").strip():
        story.summary = data["summary"]
        changed.append("summary")

    if data.get("published_at") and story.published_at is None:
        story.published_at = data["published_at"]
        changed.append("published_at")

    # updated_at is allowed to move forward: a work we indexed months ago may
    # genuinely have new chapters since, and that is the field the "recently
    # updated" sort depends on.
    upd = data.get("updated_at")
    if upd and (story.updated_at is None or upd > story.updated_at):
        story.updated_at = upd
        changed.append("updated_at")

    if data.get("word_count") and not (story.word_count or 0):
        story.word_count = data["word_count"]
        changed.append("word_count")

    if data.get("chapter_count") and (story.chapter_count or 0) < data["chapter_count"]:
        story.chapter_count = data["chapter_count"]
        changed.append("chapter_count")

    if data.get("chapter_count_total") and story.chapter_count_total is None:
        story.chapter_count_total = data["chapter_count_total"]
        changed.append("chapter_count_total")

    # AO3 states completion outright, so it may correct a stored guess — but
    # only ever toward what the page says, never back to unknown.
    st = data.get("status")
    if st is not None and story.status != st:
        story.status = st
        changed.append("status")

    if data.get("language") and not (story.language or "").strip():
        story.language = data["language"]
        changed.append("language")

    for key in ("kudos", "hits", "comments", "bookmarks"):
        val = data.get(key)
        if val is not None and val > (getattr(story, key) or 0):
            setattr(story, key, val)
            changed.append(key)

    return changed


def _flush(pending: list[tuple[int, dict]], touched: list[int], stats: dict) -> None:
    """Write harvested fields, and stamp crawled_at on everything we looked at.

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
    fetched = dict(pending)
    with db_session() as db:
        for sid in touched:
            story = db.query(Story).filter(Story.id == sid).first()
            if not story:
                continue
            if sid in fetched:
                for field in apply_work(story, fetched[sid]):
                    stats[field] = stats.get(field, 0) + 1
            story.crawled_at = datetime.now(timezone.utc)
        db.commit()
    pending.clear()
    touched.clear()


def run(limit: int, dry_run: bool, delay: float) -> None:
    with db_session() as db:
        rows = db.execute(sql_text(TRUNCATED_SQL), {"lim": limit}).fetchall()
    log.info(f"{len(rows)} truncated-looking AO3 titles to check "
             f"({WORKERS} workers, {delay}s between requests)")

    limiter = RateLimiter(delay)
    counts = {"fetched": 0, "missed": 0, "throttled": 0}
    stats: dict[str, int] = {}
    pending: list[tuple[int, dict]] = []
    touched: list[int] = []
    lock = threading.Lock()
    started = time.monotonic()

    def handle(row) -> None:
        sid, work_id, stored = row
        limiter.wait()
        data = fetch_work(client, work_id, limiter)
        # One retry after a throttle, since the limiter has now widened and
        # parked the queue. Still throttled after that: leave the row alone so
        # the next pass picks it up rather than burning it as unreachable.
        if data is THROTTLED:
            limiter.wait()
            data = fetch_work(client, work_id, limiter)

        with lock:
            if data is THROTTLED:
                # Not our answer to record — do not stamp, so it keeps its place
                # in the queue rather than being pushed to the back unexamined.
                counts["throttled"] += 1
                return

            if not data:
                counts["missed"] += 1
            else:
                counts["fetched"] += 1
                if dry_run:
                    new = (data.get("title") or "").strip()
                    if new and new != (stored or "").strip():
                        log.info(f"  {work_id}: {stored!r} -> {new!r}")
                    if data.get("summary"):
                        log.info(f"           summary: {data['summary'][:70]!r}")
                else:
                    pending.append((sid, data))

            # A dry run must not write anything at all, stamps included.
            if not dry_run:
                touched.append(sid)
                if len(touched) >= WRITE_BATCH:
                    _flush(pending, touched, stats)

    # One client for the pool so connections are reused; httpx.Client is thread-safe.
    with httpx.Client(headers=UA, limits=httpx.Limits(max_connections=WORKERS)) as client:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(handle, rows))

    with lock:
        _flush(pending, touched, stats)

    elapsed = max(time.monotonic() - started, 0.001)
    filled = " ".join(f"{k}={v}" for k, v in sorted(stats.items(), key=lambda kv: -kv[1]))
    log.info(f"DONE — fetched={counts['fetched']} unreachable={counts['missed']} "
             f"throttled={counts['throttled']} in {elapsed:.0f}s "
             f"({len(rows) / elapsed:.2f} req/s, {limiter.throttled} x 429, "
             f"interval now {limiter.interval:.2f}s)")
    log.info(f"       filled: {filled or '(nothing)'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair AO3 titles truncated by the dump")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=2.0, help="Target seconds between requests")
    args = ap.parse_args()
    run(args.limit, args.dry_run, args.delay)


if __name__ == "__main__":
    main()
