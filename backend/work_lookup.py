"""One link -> one indexed work, and fetching the work when there is none.

Two callers needed the same answer — "which work does this archive URL mean?" —
and each was missing half of it:

* A pasted link only matched the row whose own URL it was. The cross-post
  dedup keeps ONE row per story and records the other copies' URLs on it
  (live_fetch/crosspost.py), so pasting the FF.net link of a story held as its
  AO3 row found nothing. rec_threads had the same gap for recommendations.
* A link to a work the index does not hold returned an empty page. Readers
  paste links precisely when they know the work exists.

So `resolve` checks the row and then the survivors' `cross_post_urls`, and
`start_fetch` adds a missing AO3 work in the background: one work page, read
through the shared ao3_budget like every other AO3 request here, parsed by the
Wayback harvester's full work-page parser (author, fandoms, tags, WARNINGS,
rating — so the content-gate trigger has what it needs on insert), and saved by
the one insert path. Never inside the request: an AO3 round trip can wait out
the budget for minutes, and a request held that long dies at the proxy.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from sqlalchemy import text

log = logging.getLogger(__name__)

# A fetch costs AO3 one request; these keep a curious reader, or a script, from
# turning the search box into a crawler.
PER_CLIENT_PER_HOUR = 12
TOTAL_PER_HOUR = 120

_lock = threading.Lock()
_by_client: dict[str, deque] = {}
_all: deque = deque()


def copy_urls(site: str, sid: str) -> list[str]:
    """The spellings a copy's URL is stored under in `cross_post_urls`."""
    if site == "ffnet":
        return [f"{scheme}://{host}.fanfiction.net/s/{sid}{tail}"
                for scheme in ("https", "http") for host in ("www", "m")
                for tail in ("/1/", "/", "")]
    return [f"{scheme}://archiveofourown.org/works/{sid}{tail}"
            for scheme in ("https", "http") for tail in ("", "/")]


def resolve(db, site: str, sid: str):
    """The indexed work a link names (its own row, or the copy it was merged into)."""
    row = db.execute(text("SELECT id FROM stories WHERE site=:s AND site_id=:i"),
                     {"s": site, "i": sid}).first()
    if row:
        return row[0]
    row = db.execute(text(
        "SELECT id FROM stories WHERE cross_post_urls && CAST(:u AS text[]) LIMIT 1"),
        {"u": copy_urls(site, sid)}).first()
    return row[0] if row else None


def _allowed(client: str) -> bool:
    """Per-process rate caps. Approximate across processes, which is fine: the
    shared ao3_budget is what actually bounds AO3 load."""
    now = time.time()
    with _lock:
        q = _by_client.setdefault(client, deque())
        for d in (q, _all):
            while d and now - d[0] > 3600:
                d.popleft()
        if len(q) >= PER_CLIENT_PER_HOUR or len(_all) >= TOTAL_PER_HOUR:
            return False
        q.append(now); _all.append(now)
        return True


def _set_state(work_id: str, state: str) -> None:
    from db.session import db_session
    with db_session() as db:
        db.execute(text("""
            INSERT INTO pasted_fetches (work_id, state, at) VALUES (:w, :s, now())
            ON CONFLICT (work_id) DO UPDATE SET state = :s, at = now()
        """), {"w": work_id, "s": state})
        db.commit()


def _fetch(work_id: str) -> None:
    state = "missing"
    try:
        import httpx
        from ao3_title_repair import RateLimiter, RESTRICTED, THROTTLED, UA, fetch_work
        from db.session import db_session
        from live_fetch.persist import persist_live_results
        from wayback_harvest import parse_work_snapshot

        with httpx.Client(headers=UA) as client:
            data = fetch_work(client, work_id, RateLimiter(2.0),
                              parse=lambda html: parse_work_snapshot(html, int(work_id)))
        if data is RESTRICTED:
            state = "restricted"
        elif data is THROTTLED:
            state = "throttled"
        elif isinstance(data, dict):
            with db_session() as db:
                persist_live_results(db, [data])
            state = "added"
        log.info("pasted AO3 work %s: %s", work_id, state)
    except Exception:
        log.warning("pasted AO3 work %s failed", work_id, exc_info=True)
        state = "throttled"
    finally:
        _set_state(work_id, state)


def start_fetch(db, work_id: str, client: str) -> str | None:
    """Begin adding an AO3 work in the background, or report what is known.

    Returns "fetching", "restricted" (registered users only — nothing we can
    read), "missing" (deleted or not a work), or None when the caps refused.
    One claim per work across every API process: the INSERT ... ON CONFLICT
    only re-arms a row older than an hour (or one that was throttled).
    """
    if not str(work_id).isdigit():
        return None
    row = db.execute(text("""
        SELECT state, now() - at < interval '1 hour' FROM pasted_fetches WHERE work_id = :w
    """), {"w": work_id}).first()
    if row and row[1] and row[0] in ("pending", "restricted", "missing"):
        return "fetching" if row[0] == "pending" else row[0]
    if not _allowed(client or "?"):
        return None
    claimed = db.execute(text("""
        INSERT INTO pasted_fetches (work_id, state, at) VALUES (:w, 'pending', now())
        ON CONFLICT (work_id) DO UPDATE SET state = 'pending', at = now()
         WHERE pasted_fetches.at < now() - interval '1 hour'
            OR pasted_fetches.state IN ('throttled', 'added')
        RETURNING work_id
    """), {"w": work_id}).first()
    db.commit()
    if not claimed:
        return "fetching"
    threading.Thread(target=_fetch, args=(work_id,), daemon=True).start()
    return "fetching"
