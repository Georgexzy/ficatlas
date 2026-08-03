"""
Harvest AO3 metadata from the Wayback Machine instead of from AO3.
==================================================================

The summary gap is the largest single hole in the index: ~13M AO3 rows carry a
title and no summary, because the bulk dump does not ship them. Every route to
closing it so far has ended at the same wall — the data only exists on AO3, and
AO3 rate-limits to roughly 0.4 req/s per IP (see ao3_budget). At 20 works per
listing page that is still weeks of continuous crawling, and every request of it
is load on a nonprofit's servers.

Wayback changes that trade-off. archive.org has snapshotted AO3 work pages for
years, and a snapshot is a byte-for-byte copy of the page including the summary,
the tags, the dates and the stats. Fetching one costs AO3 nothing at all.

It is not free throughput, though — archive.org rate-limits at least as firmly
as AO3, and signals it by refusing connections rather than answering 429 (see
_Budget.network_error, and the measurements behind BASE_INTERVAL). So this is
not a fast route to closing the gap. What it is, is a route that does not spend
AO3's capacity, which means it can run indefinitely alongside the AO3 loops
instead of competing with them for the same budget.

This is also the collection route the OTW explicitly considers legitimate: their
statement on scraping (transformativeworks.org/ai-and-data-scraping-on-the-
archive) objects to bulk AI scraping while naming "fans backing up works to the
Wayback Machine" and search-engine indexing as acceptable uses. Reading back
what those backups already captured stays on the right side of that line.

Measured on a 57,158-work sample of the CDX index:

    18.2%  works Wayback has that we do not hold at all
    81.2%  works we DO hold but with no summary  <- the gap, exactly

So the overlap is not a coincidence to work around; it is the point. Wayback's
AO3 coverage is heavily weighted towards the same popular works whose summaries
we are missing.

Two stages, deliberately separate:

  1. CDX walk   — archive.org's index API lists every snapshot URL it holds.
                  16,387 pages of it for /works*. Cheap, no page fetches, and
                  it yields work IDs to queue.
  2. Snapshot   — fetch and parse the archived work page for a queued ID.

They are separate because the CDX walk is fast and the snapshot fetch is slow;
running them in one loop would mean the walk finishes in an hour and then idles
for months. The queue table decouples them.
"""

import html as _html
import logging
import os
import re
import threading
import time
from datetime import datetime

import httpx
from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)

CDX_URL = "http://web.archive.org/cdx/search/cdx"
SNAPSHOT = "https://web.archive.org/web/{ts}id_/https://archiveofourown.org/works/{wid}"

# Same shape of limiter as ao3_budget, tuned to what archive.org was measured to
# actually allow rather than to what a CDN "should" tolerate.
#
# The first pass ran at 1s and that was far too fast. archive.org throttles by
# refusing TCP connections rather than answering 429, so the harvest read the
# refusals as noise and kept pushing until it was blocked outright: 9 of 10
# requests refused, and still 5 of 6 refused at a 10-second gap after a minute
# of cooling off. The block outlasts the request rate that caused it, which
# means guessing upward is expensive and being wrong is not self-correcting.
#
# Hence a starting interval well above anything that drew a block, and a ceiling
# high enough that a block turns into a genuine pause rather than a permanent
# slow probe. This is still vastly cheaper than the AO3 path it replaces: the
# constraint is politeness to archive.org, not throughput.
BASE_INTERVAL = float(os.getenv("WAYBACK_MIN_INTERVAL", "5.0"))
MAX_INTERVAL = float(os.getenv("WAYBACK_MAX_INTERVAL", "600.0"))
BACKOFF = 2.0
RECOVER = 0.9
RECOVER_AFTER = 20
# Recovery is on a clock as well as a success count — see _Budget.reward. A
# throttled host cannot produce the successes that a count-only rule needs to
# climb back down, so the interval would stick at the ceiling forever.
RECOVER_EVERY = float(os.getenv("WAYBACK_RECOVER_EVERY", "120"))
# Consecutive connection failures before the interval widens. Small, because
# from archive.org these ARE the throttle signal, not transport noise.
NET_ERRORS_BEFORE_BACKOFF = int(os.getenv("WAYBACK_NET_ERRORS", "2"))

HEADERS = {
    "User-Agent": "FicAtlas/0.1 (fanfiction search index; contact: admin@ficatlas.app)",
    "Accept": "text/html,application/xhtml+xml",
}


class _Budget:
    """Process-wide pacing for archive.org, mirroring ao3_budget's contract."""

    def __init__(self) -> None:
        self.interval = BASE_INTERVAL
        self._lock = threading.Lock()
        self._next = 0.0
        self._clean = 0
        self._net_errors = 0
        self._last_recover = 0.0
        self.throttled = 0
        self.granted = 0

    def wait(self) -> None:
        with self._lock:
            start = max(time.monotonic(), self._next)
            self._next = start + self.interval
            self.granted += 1
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def penalise(self, retry_after: float | None = None) -> None:
        with self._lock:
            self.throttled += 1
            self._clean = 0
            before = self.interval
            self.interval = min(self.interval * BACKOFF, MAX_INTERVAL)
            pause = retry_after if retry_after is not None else self.interval
            self._next = max(self._next, time.monotonic() + pause)
        if self.interval != before:
            log.info(f"wayback budget: throttled -> {before:.1f}s to {self.interval:.1f}s")

    def network_error(self) -> None:
        """A connection failed — which from archive.org means "slow down".

        Measured, after this ran too fast for a while: 9 of 10 requests refused
        at TCP connect, then 1 of 10 while a cooling-off period was still in
        effect. archive.org does not answer 429 when it throttles a host; it
        refuses the connection. So a ConnectError here is not a blip to be
        tolerated, it is the rate-limit signal itself, and treating it as
        transport noise is what let the harvest keep pushing while already cut
        off.

        Still requires a short run rather than a single failure, because a real
        one-off drop should not double the interval — but the run is small, and
        recovery is time-based (see reward) so the interval cannot ratchet up
        and stay there the way it did when recovery needed consecutive successes
        that a throttled host could never produce.
        """
        with self._lock:
            self._net_errors += 1
            if self._net_errors < NET_ERRORS_BEFORE_BACKOFF:
                return
            self._net_errors = 0
        self.penalise()

    def reward(self) -> None:
        """A clean response. Narrow the interval, but only slowly and on a clock.

        Recovery was originally "RECOVER_AFTER consecutive clean requests",
        which deadlocks against a host that refuses most connections: the
        successes needed to recover are exactly what being throttled prevents.
        Requiring elapsed time instead means the interval always comes back down
        eventually, even from a long block.
        """
        now = time.monotonic()
        with self._lock:
            self._clean += 1
            self._net_errors = 0
            if self.interval <= BASE_INTERVAL:
                return
            if self._clean < RECOVER_AFTER or now - self._last_recover < RECOVER_EVERY:
                return
            self._clean = 0
            self._last_recover = now
            self.interval = max(self.interval * RECOVER, BASE_INTERVAL)

    def snapshot(self) -> dict:
        return {"interval": round(self.interval, 2), "granted": self.granted,
                "throttled": self.throttled}


BUDGET = _Budget()


# archive.org signals overload with gateway errors far more often than with 429
# — a heavy CDX query routinely comes back 504 — so these have to widen the
# interval too. Treating them as ordinary failures would mean retrying hardest
# exactly when the server is least able to answer.
BACKPRESSURE = (429, 502, 503, 504)


def note_response(status_code: int, retry_after: str | None = None) -> None:
    if status_code in BACKPRESSURE:
        try:
            after = float(retry_after) if retry_after else None
        except (TypeError, ValueError):
            after = None
        BUDGET.penalise(after)
    elif 200 <= status_code < 400:
        BUDGET.reward()


# ---------------------------------------------------------------- CDX walk

_WORK_ID_RE = re.compile(r"/works/(\d+)")


CDX_LIMIT = int(os.getenv("WAYBACK_CDX_LIMIT", "30000"))
CDX_RETRIES = int(os.getenv("WAYBACK_CDX_RETRIES", "4"))


def cdx_page(resume: str | None = None,
             timeout: float = 240.0) -> tuple[list[tuple[int, str]], str | None]:
    """One slice of the CDX index, walked by resume key.

    Returns ([(work_id, snapshot_timestamp)], next_resume_key).

    Resume keys rather than `page=N`, which does not work here. CDX pages are
    blocks of the index sorted by URL key, and AO3's index opens with tens of
    thousands of captures of the bare `/works` listing URL — so with
    collapse=urlkey an entire early page collapses to a single row. Resume keys
    paginate over *results* instead, which is what we actually want to walk.

    collapse=urlkey keeps one snapshot per URL rather than one per capture: a
    popular work has hundreds of captures carrying the same metadata.
    """
    params = [
        ("url", "archiveofourown.org/works*"),
        ("output", "json"),
        ("fl", "original,timestamp"),
        ("filter", "statuscode:200"),
        ("collapse", "urlkey"),
        ("limit", str(CDX_LIMIT)),
        ("showResumeKey", "true"),
    ]
    if resume:
        params.append(("resumeKey", resume))

    # A CDX slice is an expensive query on their side and 504s are routine, so
    # retry a few times behind the budget rather than losing the slice — giving
    # up would stall the walk at this resume key until the next pass.
    r = None
    for attempt in range(CDX_RETRIES):
        BUDGET.wait()
        try:
            r = httpx.get(CDX_URL, params=params, headers=HEADERS, timeout=timeout)
        except httpx.RequestError as e:
            log.info(f"wayback cdx: {type(e).__name__}, attempt {attempt + 1}/{CDX_RETRIES}")
            BUDGET.network_error()
            continue
        note_response(r.status_code, r.headers.get("Retry-After"))
        if r.status_code not in BACKPRESSURE:
            break
        log.info(f"wayback cdx: HTTP {r.status_code}, attempt {attempt + 1}/{CDX_RETRIES}")

    if r is None or r.status_code != 200:
        raise httpx.HTTPError(
            f"CDX unavailable after {CDX_RETRIES} attempts"
            f"{f' (HTTP {r.status_code})' if r is not None else ''}")
    if not r.text.strip():
        return [], None

    import json
    rows = json.loads(r.text)
    if rows and rows[0] and rows[0][0] == "original":
        rows = rows[1:]           # header row, present only sometimes

    # The resume key arrives as the last row, preceded by a blank row. Its
    # absence is how the walk learns it has reached the end of the index.
    next_key = None
    if len(rows) >= 2 and rows[-1] and not rows[-2]:
        next_key = rows[-1][0]
        rows = rows[:-2]

    out: dict[int, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        m = _WORK_ID_RE.search(row[0])
        if not m:
            continue
        wid = int(m.group(1))
        # Work IDs are positive; /works/0 and friends are junk redirects.
        if wid <= 0:
            continue
        # Keep the LATEST snapshot per work — metadata like kudos and chapter
        # count only improves with time, and a 2011 capture of a still-updating
        # fic would overwrite good data with stale. Chapter URLs collapse into
        # the same work here, which is why the row count exceeds the work count.
        prev = out.get(wid)
        if prev is None or row[1] > prev:
            out[wid] = row[1]
    return sorted(out.items()), next_key


def queue_ids(db, pairs: list[tuple[int, str]]) -> int:
    """Add discovered work IDs to the queue. Returns how many were new."""
    if not pairs:
        return 0
    values = ",".join(f"({w},'{t}')" for w, t in pairs if t.isdigit())
    if not values:
        return 0
    res = db.execute(sql_text(f"""
        INSERT INTO wayback_queue (work_id, snapshot_ts) VALUES {values}
        ON CONFLICT (work_id) DO NOTHING
    """))
    return res.rowcount or 0


# ------------------------------------------------------------ snapshot parse

def _tags_in(block: str | None) -> list[str]:
    if not block:
        return []
    return [_html.unescape(re.sub(r"\s+", " ", t).strip())
            for t in re.findall(r'<a[^>]*class="tag"[^>]*>(.*?)</a>', block, re.S)]


def _dd(html_text: str, cls: str) -> str | None:
    m = re.search(rf'<dd class="{cls}"[^>]*>(.*?)</dd>', html_text, re.S)
    return m.group(1) if m else None


def _text(block: str | None) -> str | None:
    if block is None:
        return None
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block)).strip()
    return _html.unescape(t) or None


def _int(block: str | None) -> int | None:
    t = _text(block)
    if not t:
        return None
    m = re.search(r"[\d,]+", t)
    return int(m.group(0).replace(",", "")) if m else None


def parse_work_snapshot(html_text: str, work_id: int) -> dict | None:
    """Parse an archived AO3 work page into a persist_live_results entry.

    This is NOT parse_works_page: a work page and a listing blurb use different
    markup (`blockquote.userstuff` inside `div.summary` versus
    `blockquote.userstuff.summary`, `dd.published` versus `p.datetime`), so the
    listing parser silently returns nothing on these pages.
    """
    if "<h2 class=\"title heading\">" not in html_text:
        return None                     # deleted work, login wall, or error page

    title = _text(re.search(r'<h2 class="title heading">(.*?)</h2>', html_text, re.S).group(1))
    if not title:
        return None

    am = re.search(r'<a rel="author"[^>]*>(.*?)</a>', html_text, re.S)
    author = _text(am.group(1)) if am else None

    summary = None
    sm = re.search(r'<div class="summary module".*?<blockquote class="userstuff">(.*?)</blockquote>',
                   html_text, re.S)
    if sm:
        summary = (_text(sm.group(1)) or "")[:2000] or None

    relationships = _tags_in(_dd(html_text, "relationship tags"))
    characters = _tags_in(_dd(html_text, "character tags"))
    freeforms = _tags_in(_dd(html_text, "freeform tags"))
    fandoms = _tags_in(_dd(html_text, "fandom tags"))
    warnings = _tags_in(_dd(html_text, "warning tags"))
    categories = _tags_in(_dd(html_text, "category tags"))
    rating = (_tags_in(_dd(html_text, "rating tags")) or [None])[0]

    chapters = _text(_dd(html_text, "chapters")) or ""
    cm = re.match(r"(\d+)\s*/\s*(\d+|\?)", chapters)
    chapter_count = int(cm.group(1)) if cm else None
    total = cm.group(2) if cm else None
    chapter_count_total = int(total) if (total or "").isdigit() else None

    def _date(cls: str) -> str | None:
        raw = _text(_dd(html_text, cls))
        if not raw:
            return None
        try:
            return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").isoformat()
        except ValueError:
            return None

    published = _date("published")
    # AO3 shows `dd.status` ("Updated"/"Completed") only on multi-chapter works;
    # for a one-shot the publish date is also the last-changed date.
    updated_at = _date("status") or published

    status = "unknown"
    if chapter_count and chapter_count_total:
        status = "complete" if chapter_count >= chapter_count_total else "in_progress"

    return {
        "id":            f"live_ao3_{work_id}",
        "site_id":       str(work_id),
        "url":           f"https://archiveofourown.org/works/{work_id}",
        "title":         title,
        "author":        author,
        "summary":       summary,
        "published_at":  published,
        "updated_at":    updated_at,
        "fandoms":       fandoms,
        "characters":    characters,
        "relationships": relationships,
        "tags":          relationships + characters + freeforms,
        "rating":        rating,
        "status":        status,
        "language":      _text(_dd(html_text, "language")) or "English",
        "word_count":    _int(_dd(html_text, "words")),
        "chapter_count": chapter_count,
        "chapter_count_total": chapter_count_total,
        "kudos":         _int(_dd(html_text, "kudos")),
        "hits":          _int(_dd(html_text, "hits")),
        "bookmarks":     _int(_dd(html_text, "bookmarks")),
        "comments":      _int(_dd(html_text, "comments")),
        "warnings":      warnings,
        "categories":    categories,
    }


class Transient(Exception):
    """archive.org could not answer right now; the work itself is fine.

    This has to be distinguishable from "there is no usable snapshot", because
    the two need opposite handling. A work with no snapshot should be marked
    done so the queue moves past it; a work we failed to *reach* must stay
    queued. Collapsing both into None burned 191 works in the first ten minutes
    — written off permanently because archive.org was briefly throttling.
    """


def fetch_snapshot(work_id: int, ts: str, timeout: float = 90.0) -> dict | None:
    """Fetch one archived work page.

    Returns a parsed entry, or None if the snapshot exists but holds no usable
    work page (deleted work, login wall, error capture). Raises Transient if
    archive.org could not be reached or asked us to back off.
    """
    BUDGET.wait()
    try:
        r = httpx.get(SNAPSHOT.format(ts=ts, wid=work_id), headers=HEADERS,
                      timeout=timeout, follow_redirects=True)
    except httpx.RequestError as e:
        BUDGET.network_error()
        raise Transient(type(e).__name__) from e
    note_response(r.status_code, r.headers.get("Retry-After"))
    if r.status_code in BACKPRESSURE:
        raise Transient(f"HTTP {r.status_code}")
    if r.status_code != 200:
        # 404 and friends are real answers: this snapshot will never resolve,
        # so let the caller retire it.
        return None
    return parse_work_snapshot(r.text, work_id)


# ------------------------------------------------------------------- queue

# Works we already hold but cannot summarise are the reason this exists, so they
# go first; works we do not hold at all come after. Ordering by work_id within
# each band keeps the walk stable across restarts.
NEXT_SQL = sql_text("""
    SELECT q.work_id, q.snapshot_ts
    FROM wayback_queue q
    LEFT JOIN stories s ON s.site = 'ao3' AND s.site_id = q.work_id::text
    WHERE q.done_at IS NULL
    ORDER BY (s.id IS NOT NULL AND (s.summary IS NULL OR s.summary = '')) DESC,
             q.work_id
    LIMIT :lim
""")


def next_batch(db, limit: int = 20) -> list[tuple[int, str]]:
    return [(r[0], r[1]) for r in db.execute(NEXT_SQL, {"lim": limit}).fetchall()]


def mark_done(db, work_id: int, ok: bool) -> None:
    db.execute(sql_text("""
        UPDATE wayback_queue SET done_at = now(), ok = :ok WHERE work_id = :w
    """), {"w": work_id, "ok": ok})


def queue_stats(db) -> dict:
    row = db.execute(sql_text("""
        SELECT count(*) FILTER (WHERE done_at IS NULL),
               count(*) FILTER (WHERE done_at IS NOT NULL AND ok),
               count(*) FILTER (WHERE done_at IS NOT NULL AND NOT ok)
        FROM wayback_queue
    """)).first()
    return {"pending": row[0], "done": row[1], "failed": row[2],
            "budget": BUDGET.snapshot()}
