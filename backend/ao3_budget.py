"""
One AO3 request budget, shared by every loop AND every process.
============================================================

Five separate loops now talk to AO3 — title repair, the listing harvest, the
recent-works walk, the stale refresh and the alternative-archive import — and
each was pacing itself in isolation. Individually every one of them was polite.
Together they were not: 244 requests in ten minutes with ten 429s, and the
symptom was a loop that only ever makes one request per pass (the listing
harvest fetching page 1) getting rejected because another loop had already
spent the budget.

Self-pacing does not compose. A limiter has to be global to the process or it
is not a limit on anything, so this is a module-level singleton every AO3 path
goes through.

It also carries the 429 response back: when AO3 refuses ANY caller, all of them
slow down. Previously each loop learned about rate limiting separately, so four
of them would keep hammering while the fifth backed off politely.

Adaptive, for the reason set out in ao3_title_repair: AO3's robots.txt sets no
Crawl-delay and their published position (admin_posts/25888) says only that
they rate-limit and watch for abuse, so the real threshold is not documented
anywhere and has to be discovered. Widen hard on refusal, recover slowly on
success.
"""

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

# Starting interval between AO3 requests, process-wide. Measured: ~0.4 req/s
# across all loops drew 429s, so the default sits below that and the limiter
# adapts from there rather than trusting the number.
BASE_INTERVAL = float(os.getenv("AO3_MIN_INTERVAL", "3.0"))
MIN_INTERVAL = 1.0
MAX_INTERVAL = 60.0
BACKOFF = 2.0        # on 429 — fast, because being over the line costs AO3
RECOVER = 0.9        # per recovery step — slow, because guessing upward is rude
RECOVER_AFTER = 10   # consecutive clean requests per step


# Cross-process coordination, via the one thing every FicAtlas process already
# shares: Postgres.
#
# The in-memory budget below is per-PROCESS, and the docstring at the top of this
# file used to say "shared by every loop in the process" without anyone noticing
# what that implied. The worker runs the harvest, the feed poller and the FF.net
# enrichment inside one process, so those three do share. But a maintenance
# script run with `docker compose exec backend python ...` is a SEPARATE process
# with its own _Budget starting fresh at BASE_INTERVAL — so AO3 saw the sum of
# two independent limiters, each politely believing it was the only one.
#
# A ticket is claimed by advancing a single row: next_at = max(now, next_at) +
# interval, returned to the caller, committed immediately. The row lock is held
# for the arithmetic only and never across the sleep, so N processes queue up
# behind each other instead of serialising on a held transaction.
_TICKET_SQL = """
    INSERT INTO crawl_budget (host, next_at, interval_s)
    VALUES (:host, now(), :interval)
    ON CONFLICT (host) DO UPDATE
        SET next_at = GREATEST(crawl_budget.next_at, now())
                    + make_interval(secs => EXCLUDED.interval_s)
    RETURNING next_at, interval_s
"""

_shared_ready = False
_shared_failed = False


def _claim_shared_slot(host: str, interval: float) -> float | None:
    """Seconds to wait for this process's turn, or None if the shared budget is
    unavailable (no database, table missing) — in which case the caller falls
    back to the in-memory limiter rather than hammering unthrottled."""
    global _shared_ready, _shared_failed
    if _shared_failed:
        return None
    try:
        from sqlalchemy import text as _t
        from db.session import db_session
        with db_session() as db:
            if not _shared_ready:
                db.execute(_t("""
                    CREATE TABLE IF NOT EXISTS crawl_budget (
                        host       TEXT PRIMARY KEY,
                        next_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                        interval_s DOUBLE PRECISION NOT NULL DEFAULT 5.0
                    )
                """))
                db.commit()
                _shared_ready = True
            row = db.execute(_t(_TICKET_SQL),
                             {"host": host, "interval": interval}).first()
            db.commit()
        if not row:
            return None
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        return max(0.0, (row[0] - now).total_seconds())
    except Exception as e:                     # no DB, migration not run, etc.
        _shared_failed = True
        log.warning(f"AO3 budget: shared limiter unavailable ({type(e).__name__}) "
                    f"— falling back to this process only")
        return None


def _publish_penalty(host: str, interval: float, pause: float) -> None:
    """Push a backoff into the shared row: widen the interval AND push the next
    slot out, so processes that are mid-sleep do not sail straight through."""
    if _shared_failed:
        return
    try:
        from sqlalchemy import text as _t
        from db.session import db_session
        with db_session() as db:
            db.execute(_t("""
                UPDATE crawl_budget
                   SET interval_s = GREATEST(interval_s, :interval),
                       next_at    = GREATEST(next_at, now() + make_interval(secs => :pause))
                 WHERE host = :host
            """), {"host": host, "interval": interval, "pause": pause})
            db.commit()
    except Exception:
        pass                                   # advisory; never break a crawl


class _Budget:
    def __init__(self, host: str = "archiveofourown.org") -> None:
        self.interval = BASE_INTERVAL
        self.host = host
        self._lock = threading.Lock()
        self._next = 0.0
        self._clean = 0
        self.throttled = 0
        self.granted = 0

    def wait(self) -> None:
        """Block until this caller may make one request against self.host."""
        # Shared queue first. Every process claims from the same row, so the
        # rate AO3 sees is the configured one no matter how many are running.
        shared_delay = _claim_shared_slot(self.host, self.interval)

        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
            self.granted += 1
        delay = start - time.monotonic()

        # Whichever queue says wait longer wins: the in-memory one still carries
        # this process's own backoff state, which the shared row does not know
        # about until penalise() writes it.
        if shared_delay is not None:
            delay = max(delay, shared_delay)
        if delay > 0:
            time.sleep(delay)

    def penalise(self, retry_after: float | None = None) -> None:
        """AO3 refused someone. Everyone slows down."""
        with self._lock:
            self.throttled += 1
            self._clean = 0
            before = self.interval
            self.interval = min(self.interval * BACKOFF, MAX_INTERVAL)
            # Honour Retry-After against the shared queue, not just the caller
            # that happened to receive it — the limit is on the process.
            pause = retry_after if retry_after is not None else self.interval
            self._next = max(self._next, time.monotonic() + pause)
        # Publish the penalty so OTHER processes back off as well. Without this
        # a script would keep its own optimistic interval while the worker was
        # being told to slow down, which is the failure the shared row exists to
        # prevent.
        _publish_penalty(self.host, self.interval, pause)
        if self.interval != before:
            log.info(f"AO3 budget: 429 -> interval {before:.1f}s to {self.interval:.1f}s")

    def reward(self) -> None:
        with self._lock:
            self._clean += 1
            if self._clean >= RECOVER_AFTER and self.interval > BASE_INTERVAL:
                self._clean = 0
                self.interval = max(self.interval * RECOVER, BASE_INTERVAL, MIN_INTERVAL)

    def snapshot(self) -> dict:
        return {"interval": round(self.interval, 2), "granted": self.granted,
                "throttled": self.throttled}


BUDGET = _Budget()


def wait() -> None:
    BUDGET.wait()


async def await_slot() -> None:
    """Async callers get the same budget; the wait itself is off the loop."""
    import asyncio
    await asyncio.to_thread(BUDGET.wait)


def note_response(status_code: int, retry_after: str | None = None) -> None:
    """Feed an AO3 response back into the budget."""
    if status_code == 429:
        try:
            after = float(retry_after) if retry_after else None
        except (TypeError, ValueError):
            after = None
        BUDGET.penalise(after)
    elif 200 <= status_code < 400:
        BUDGET.reward()


# ── Other Otwarchive hosts ──────────────────────────────────────────────────
#
# SquidgeWorld runs the same software as AO3, so the same scraper points at it —
# and because the budget above was gated on `is_ao3`, those requests went out
# with NO pacing at all. AO3 is a large nonprofit with real infrastructure;
# SquidgeWorld is a small volunteer-run archive, so it is the one that least
# deserves an unthrottled crawler, and it was the only one getting one.
#
# That is why SquidgeWorld is not in the worker's ARCHIVES list: adding it would
# have pointed an unpaced loop at it. One budget per host fixes that, and the
# default is deliberately slower than AO3's since these hosts are smaller.
_HOST_BUDGETS: dict[str, "_Budget"] = {}
_HOST_LOCK = threading.Lock()
OTHER_HOST_INTERVAL = float(os.getenv("OTWARCHIVE_MIN_INTERVAL", "5.0"))


def for_host(base_url: str | None) -> "_Budget":
    """The budget governing one host. AO3 shares the process-wide one."""
    if not base_url or "archiveofourown.org" in base_url:
        return BUDGET
    host = base_url.split("//", 1)[-1].split("/", 1)[0].lower()
    with _HOST_LOCK:
        b = _HOST_BUDGETS.get(host)
        if b is None:
            b = _Budget()
            b.interval = OTHER_HOST_INTERVAL
            _HOST_BUDGETS[host] = b
            log.info(f"pacing {host} at {OTHER_HOST_INTERVAL}s between requests")
        return b


async def await_slot_for(base_url: str | None) -> None:
    import asyncio
    await asyncio.to_thread(for_host(base_url).wait)


def note_response_for(base_url: str | None, status_code: int,
                      retry_after: str | None = None) -> None:
    b = for_host(base_url)
    if status_code == 429:
        try:
            after = float(retry_after) if retry_after else None
        except (TypeError, ValueError):
            after = None
        b.penalise(after)
    elif 200 <= status_code < 400:
        b.reward()
