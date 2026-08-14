"""
Anonymous traffic — what the site is used for, not who used it.
===============================================================

The site had no idea what happened on it. Not "an incomplete picture": none at
all. Whether the SEO work landed, whether anybody searches for fandoms the index
is thin on, whether a link from somewhere sent real readers — every one of those
was unanswerable except by watching nginx scroll past.

Two things are recorded, and nothing else:

    page    a page a browser actually rendered, reported by the app itself
    search  a query someone ran, with what it found

WHAT IS DELIBERATELY NOT STORED
-------------------------------
No IP address. No user agent string. No account id, not even for signed-in
readers — an owner browsing their own site is one more anonymous row. No
referring URL, only its host.

A visitor is a 16-hex-character keyed hash of (day, address, user agent). The
day is in the hash, so the same person is one visitor within a day and an
unrelated one tomorrow: sessions and bounce rate stay answerable, and following
somebody week to week is not. The key lives in its own table rather than in
app_settings, which is world-readable through GET /api/settings — a key you can
read is a key you can use to check a guess, and being unable to check a guess is
the entire property being bought here.

This is the reason the pageview arrives as a beacon from the browser rather than
being taken off the server's own request log: the server sees every asset fetch,
every prefetch and every crawler, and separating "a person looked at this page"
out of that reliably means keeping more about each request, not less. The cost
is that crawlers are not counted at all, because they do not run the beacon.
That is the right trade for a question about people, and the wrong one if the
question is ever "is Googlebot getting round the site" — that needs the access
log, not this table.

WRITE PATH
----------
Buffered, and dropped rather than queued without bound. A pageview must never be
able to slow a page down or fail one, so `record()` takes a lock, appends a tuple
and returns; a background task writes them in batches. If the database is
unavailable or something floods us, the buffer hits its cap and new events are
counted and discarded. Losing traffic samples is a non-event. Making the site
wait on its own analytics is not.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import text

log = logging.getLogger(__name__)

ENABLED = os.getenv("TRACKING", "true").strip().lower() in ("1", "true", "yes")

# How long rows are kept. Ninety days answers "is this month better than last"
# and is short enough that the table stays small on a home server.
RETENTION_DAYS = int(os.getenv("TRACKING_RETENTION_DAYS", "90"))

# Buffer limits. FLUSH_SECONDS is how long an event can sit unwritten, MAX_BUFFER
# is where we start dropping instead of growing.
FLUSH_SECONDS = float(os.getenv("TRACKING_FLUSH_SEC", "5"))
MAX_BUFFER = int(os.getenv("TRACKING_MAX_BUFFER", "5000"))

# Substring match on a lowercased user agent. Not a serious classifier — a
# crawler that wants to look like Chrome will — but it costs nothing and keeps
# the obvious automated traffic out of the human numbers. The user agent itself
# is used here and then thrown away; only this boolean is stored.
_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|scrape|curl|wget|python-requests|httpx|"
    r"headless|lighthouse|monitor|preview|facebookexternalhit|"
    r"embedly|pingdom|uptime",
    re.I,
)

_buf: list[dict] = []
_lock = threading.Lock()
_dropped = 0

_secret: Optional[bytes] = None
_secret_lock = threading.Lock()


def _load_secret() -> bytes:
    """The hashing key, generated once and kept.

    It has to survive a restart. If it did not, every blue/green deploy would
    re-anonymise the whole audience and each release would look like a flood of
    new visitors — which is precisely the number an operator would read as
    "something I did worked".
    """
    global _secret
    if _secret is not None:
        return _secret
    with _secret_lock:
        if _secret is not None:
            return _secret
        env = os.getenv("TRACKING_SECRET", "").strip()
        if env:
            _secret = env.encode()
            return _secret
        from db.session import db_session
        with db_session() as db:
            row = db.execute(text("SELECT secret FROM tracking_secret")).first()
            if row:
                _secret = row[0].encode()
                return _secret
            fresh = secrets.token_urlsafe(32)
            # ON CONFLICT so two workers starting together cannot race each
            # other into an error; whichever loses re-reads the winner's key
            # rather than overwriting it, or the two would hash differently.
            db.execute(text("""
                INSERT INTO tracking_secret (one_row, secret) VALUES (TRUE, :s)
                ON CONFLICT (one_row) DO NOTHING
            """), {"s": fresh})
            db.commit()
            row = db.execute(text("SELECT secret FROM tracking_secret")).first()
            _secret = (row[0] if row else fresh).encode()
        return _secret


def visitor_hash(ip: str, ua: str, when: Optional[datetime] = None) -> str:
    """A stable-for-today, meaningless-tomorrow identifier for one visitor."""
    day = (when or datetime.utcnow()).strftime("%Y-%m-%d")
    msg = f"{day}|{ip or '?'}|{ua or '?'}".encode()
    return hmac.new(_load_secret(), msg, hashlib.sha256).hexdigest()[:16]


def is_bot(ua: str) -> bool:
    return bool(ua and _BOT_RE.search(ua))


def ref_host(referer: str) -> Optional[str]:
    """The host a referrer points at, or None.

    Only the host, never the path: see the module docstring. Whether a referrer
    is external is decided by the caller — the browser is the only party that
    knows both the document's referrer and its own origin, and the beacon POST's
    own Referer header is useless here because it always names the page that
    sent it.
    """
    if not referer:
        return None
    try:
        host = (urlsplit(referer).hostname or "").lower()
    except Exception:
        return None
    if not host:
        return None
    return host[:120]


def record(kind: str, path: str, visitor: str, *,
           ref: Optional[str] = None, q: Optional[str] = None,
           results: Optional[int] = None, bot: bool = False) -> None:
    """Queue one event. Never raises, never blocks on I/O."""
    global _dropped
    if not ENABLED:
        return
    try:
        row = {
            "at": datetime.utcnow(),
            "visitor": visitor[:16],
            "kind": kind[:8],
            "path": (path or "/")[:300],
            "ref_host": (ref or None),
            "q": (q[:200] if q else None),
            "results": results,
            "bot": bool(bot),
        }
    except Exception:
        return
    with _lock:
        if len(_buf) >= MAX_BUFFER:
            _dropped += 1
            return
        _buf.append(row)


def flush() -> int:
    """Write whatever is buffered. Returns the number of rows written.

    Blocking; call it off the event loop. On failure the batch is discarded
    rather than retried — a retry queue that grows while the database is down is
    the thing that turns an analytics outage into a memory problem.
    """
    global _dropped
    with _lock:
        if not _buf:
            if _dropped:
                log.warning(f"tracking: dropped {_dropped} events (buffer full)")
                _dropped = 0
            return 0
        batch, _buf[:] = list(_buf), []
        dropped, _dropped = _dropped, 0
    if dropped:
        log.warning(f"tracking: dropped {dropped} events (buffer full)")
    try:
        from db.session import db_session
        with db_session() as db:
            db.execute(text("""
                INSERT INTO visit_events
                    (at, visitor, kind, path, ref_host, q, results, bot)
                VALUES
                    (:at, :visitor, :kind, :path, :ref_host, :q, :results, :bot)
            """), batch)
            db.commit()
        return len(batch)
    except Exception as e:
        log.warning(f"tracking: flush of {len(batch)} events failed: {type(e).__name__}: {e}")
        return 0


def prune() -> int:
    """Drop events past the retention window. Returns rows deleted."""
    try:
        from db.session import db_session
        with db_session() as db:
            r = db.execute(
                text("DELETE FROM visit_events WHERE at < :cut"),
                {"cut": datetime.utcnow() - timedelta(days=RETENTION_DAYS)},
            )
            db.commit()
            return r.rowcount or 0
    except Exception as e:
        log.warning(f"tracking: prune failed: {type(e).__name__}")
        return 0


async def flush_loop(stop_after_idle: Optional[float] = None) -> None:
    """Background writer. Started from the API's lifespan.

    Cancellation is expected — it is how shutdown arrives — and the final flush
    runs on the way out so the last few seconds of traffic are not lost on every
    deploy.
    """
    import asyncio
    last_prune = time.monotonic()
    try:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            await asyncio.to_thread(flush)
            # Hourly, and only from one place, so retention does not need its own
            # scheduler entry or a cron container.
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                n = await asyncio.to_thread(prune)
                if n:
                    log.info(f"tracking: pruned {n} events older than {RETENTION_DAYS}d")
    except asyncio.CancelledError:
        await asyncio.to_thread(flush)
        raise
