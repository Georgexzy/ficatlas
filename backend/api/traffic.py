"""
Traffic — the beacon that records a pageview, and the reports that read them.
============================================================================

The write side is public and deliberately dull: a browser says "I rendered this
path", the server decides everything that gets stored about it, and the reply is
204 with no body. Nothing a caller sends is trusted beyond the path and the
referrer, and the visitor identity is derived here, never accepted.

The read side is OWNER-only, which is a stronger gate than the rest of /admin
uses (that is `admin`, and covers imports and crawls). Traffic is a different
kind of thing to know: an admin is trusted to run the library, and this is a
record of other people's behaviour on it — what they searched for, what they
read, where they came from. On an instance with more than one operator that
distinction is the whole difference between "can manage the index" and "can read
over the audience's shoulder", so it sits with the role that already governs
other people's accounts.

See tracking.py for what is and is not stored, and why the pageview arrives from
the browser rather than off the request log.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

import tracking
from api.auth import require_owner
from db.session import get_db
from ratelimit import client_ip

log = logging.getLogger(__name__)

router = APIRouter()


# ── write ───────────────────────────────────────────────────────────────────

@router.post("/hit", status_code=204)
def hit(request: Request, path: str = Form(...), ref: str = Form("")):
    """Record one pageview. Public, unauthenticated, and answers nothing.

    204 with no body on every outcome, including a path this refuses to store.
    A beacon that reports its own success gives an abuser a way to tune their
    input, and gives the page nothing it can use — it has already rendered.

    The caller supplies WHAT was viewed. It does not supply who viewed it: the
    visitor hash is derived from the connection here (see tracking.visitor_hash)
    precisely so a caller cannot claim to be someone else, or claim to be a
    thousand people.
    """
    # Only site-relative paths. Without this the table would accept
    # "https://example.com/whatever" and the top-pages report becomes a
    # stranger's billboard.
    if not path.startswith("/") or path.startswith("//"):
        return Response(status_code=204)

    ua = request.headers.get("user-agent", "")
    # `ref` and not this request's own Referer header: the beacon is a POST made
    # BY the page, so its Referer is always the page itself. Falling back to it
    # would make every row a self-referral and the referrers report a list of
    # one host. The caller sends the document's referrer, and only when it is
    # external — see NavRecorder.
    try:
        tracking.record(
            "page", path,
            tracking.visitor_hash(client_ip(request), ua),
            ref=tracking.ref_host(ref),
            bot=tracking.is_bot(ua),
        )
    except Exception:
        # Analytics must never be able to fail a request. There is nothing the
        # caller could do about it and nothing it would change on the page.
        log.debug("tracking: beacon failed", exc_info=True)
    return Response(status_code=204)


# ── read (owner only) ───────────────────────────────────────────────────────

def _window(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=max(1, min(days, 365)))


@router.get("/summary")
def summary(days: int = Query(30, ge=1, le=365),
            include_bots: bool = Query(False),
            db: Session = Depends(get_db),
            _owner=Depends(require_owner)):
    """Day-by-day visitors, pageviews and searches, plus the totals."""
    bots = "" if include_bots else "AND NOT bot"
    rows = db.execute(text(f"""
        SELECT at::date                             AS day,
               count(*) FILTER (WHERE kind='page')   AS views,
               count(*) FILTER (WHERE kind='search') AS searches,
               count(DISTINCT visitor)               AS visitors
        FROM visit_events
        WHERE at >= :cut {bots}
        GROUP BY 1 ORDER BY 1
    """), {"cut": _window(days)}).fetchall()

    # Unique visitors do not add up across days — the hash is per-day by
    # design, so the same person is a different visitor tomorrow. Summing the
    # column would count a daily regular thirty times and call it thirty people.
    # The honest total is the sum for pageviews and searches, and a per-day
    # figure for visitors, so both are given and named for what they are.
    return {
        "days": [{
            "day": r[0].isoformat(), "views": r[1],
            "searches": r[2], "visitors": r[3],
        } for r in rows],
        "totals": {
            "views": sum(r[1] for r in rows),
            "searches": sum(r[2] for r in rows),
            "busiest_day_visitors": max((r[3] for r in rows), default=0),
        },
        "retention_days": tracking.RETENTION_DAYS,
        "enabled": tracking.ENABLED,
    }


@router.get("/pages")
def pages(days: int = Query(30, ge=1, le=365),
          limit: int = Query(30, ge=1, le=200),
          db: Session = Depends(get_db),
          _owner=Depends(require_owner)):
    """Most-viewed paths."""
    rows = db.execute(text("""
        SELECT path, count(*) AS views, count(DISTINCT visitor) AS visitors
        FROM visit_events
        WHERE at >= :cut AND kind = 'page' AND NOT bot
        GROUP BY path ORDER BY views DESC LIMIT :lim
    """), {"cut": _window(days), "lim": limit}).fetchall()
    return {"pages": [{"path": r[0], "views": r[1], "visitors": r[2]} for r in rows]}


@router.get("/searches")
def searches(days: int = Query(30, ge=1, le=365),
             limit: int = Query(30, ge=1, le=200),
             db: Session = Depends(get_db),
             _owner=Depends(require_owner)):
    """What people searched for, and how much of it the index could answer.

    `empty` is the report worth having. A search that returned nothing is a
    reader who left with nothing, and it names the gap precisely — which is a
    far more direct answer to "what should be crawled next" than any coverage
    percentage.
    """
    cut = _window(days)
    top = db.execute(text("""
        SELECT lower(q) AS query, count(*) AS runs,
               count(DISTINCT visitor) AS visitors,
               max(results) AS best_results
        FROM visit_events
        WHERE at >= :cut AND kind = 'search' AND NOT bot AND q IS NOT NULL AND q <> ''
        GROUP BY 1 ORDER BY runs DESC LIMIT :lim
    """), {"cut": cut, "lim": limit}).fetchall()
    empty = db.execute(text("""
        SELECT lower(q) AS query, count(*) AS runs
        FROM visit_events
        WHERE at >= :cut AND kind = 'search' AND NOT bot
          AND q IS NOT NULL AND q <> '' AND results = 0
        GROUP BY 1 ORDER BY runs DESC LIMIT :lim
    """), {"cut": cut, "lim": limit}).fetchall()
    return {
        "top": [{"query": r[0], "runs": r[1], "visitors": r[2],
                 "results": r[3]} for r in top],
        "empty": [{"query": r[0], "runs": r[1]} for r in empty],
    }


@router.get("/referrers")
def referrers(days: int = Query(30, ge=1, le=365),
              limit: int = Query(30, ge=1, le=200),
              db: Session = Depends(get_db),
              _owner=Depends(require_owner)):
    """Which other sites send readers here. Self-referrals are already dropped
    at write time, so everything in this list is genuinely external."""
    rows = db.execute(text("""
        SELECT ref_host, count(*) AS hits, count(DISTINCT visitor) AS visitors
        FROM visit_events
        WHERE at >= :cut AND NOT bot AND ref_host IS NOT NULL
        GROUP BY 1 ORDER BY hits DESC LIMIT :lim
    """), {"cut": _window(days), "lim": limit}).fetchall()
    return {"referrers": [{"host": r[0], "hits": r[1], "visitors": r[2]}
                          for r in rows]}
