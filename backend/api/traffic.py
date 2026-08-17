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
import re
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


# ── making the paths readable ───────────────────────────────────────────────
#
# Half the routes on this site are addressed by opaque id, so the top-pages
# report reads as
#
#     /story/4b15fe7e-51aa-46c6-b8ec-f0738c8e7b3c/chapter/58    4 views
#
# which says nothing about what anyone read. The titles are resolved HERE, at
# report time, rather than being stored alongside the pageview:
#
#   * tracking.py's table is deliberately minimal and its docstring asks that
#     you think before adding a column. This needs no column.
#   * a title recorded at view time freezes; resolving on read always shows the
#     work's current title, and follows a retitle rather than preserving a
#     stale copy of it.
#   * it costs a handful of primary-key lookups against a list already capped
#     at `limit` rows, on an owner-only page nobody loads in a loop.
#
# An id that no longer resolves — a withdrawn work, a rebuilt hub — keeps its
# raw path. Showing the path is honest about not knowing; inventing a label
# would not be.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_STORY = re.compile(rf"^/story/({_UUID})(?:/chapter/(\d+))?/?$", re.I)
_SERIES = re.compile(rf"^/series/({_UUID})/?$", re.I)
_HUB = re.compile(r"^/(fandom|ship)/([a-z0-9-]+)/?$", re.I)


def _lookup(db, sql: str, keys: set) -> dict[str, str]:
    if not keys:
        return {}
    try:
        return {str(r[0]): r[1] for r in
                db.execute(text(sql), {"k": list(keys)}).fetchall()}
    except Exception:
        # A report that renders raw paths is much better than one that 500s.
        log.debug("traffic: label lookup failed", exc_info=True)
        return {}


def _labels(db, paths: list[str]) -> dict[str, str]:
    """path -> human-readable label, for the paths that have one."""
    stories, series, fandoms, ships = set(), set(), set(), set()
    for p in paths:
        if m := _STORY.match(p):
            stories.add(m.group(1).lower())
        elif m := _SERIES.match(p):
            series.add(m.group(1).lower())
        elif m := _HUB.match(p):
            (fandoms if m.group(1).lower() == "fandom" else ships).add(
                m.group(2).lower())

    titles = _lookup(db, "SELECT id, title FROM stories WHERE id = ANY(CAST(:k AS uuid[]))", stories)
    snames = _lookup(db, "SELECT id, name FROM series WHERE id = ANY(CAST(:k AS uuid[]))", series)
    fnames = _lookup(db, "SELECT slug, name FROM fandom_hubs WHERE slug = ANY(:k)", fandoms)
    shnames = _lookup(db, "SELECT slug, name FROM ship_hubs WHERE slug = ANY(:k)", ships)

    out: dict[str, str] = {}
    for p in paths:
        if m := _STORY.match(p):
            title = titles.get(m.group(1).lower())
            if title:
                # The chapter number stays on the label. Chapters are separate
                # rows in this report and collapsing them would hide the one
                # thing the chapter path actually tells you — how far into a
                # long work people are getting.
                out[p] = f"{title} — ch {m.group(2)}" if m.group(2) else title
        elif m := _SERIES.match(p):
            if name := snames.get(m.group(1).lower()):
                out[p] = f"{name} (series)"
        elif m := _HUB.match(p):
            kind = m.group(1).lower()
            src = fnames if kind == "fandom" else shnames
            if name := src.get(m.group(2).lower()):
                out[p] = f"{name} ({'fandom' if kind == 'fandom' else 'pairing'})"
    return out


@router.get("/pages")
def pages(days: int = Query(30, ge=1, le=365),
          limit: int = Query(30, ge=1, le=200),
          db: Session = Depends(get_db),
          _owner=Depends(require_owner)):
    """Most-viewed paths, with ids resolved to titles where they have one."""
    rows = db.execute(text("""
        SELECT path, count(*) AS views, count(DISTINCT visitor) AS visitors
        FROM visit_events
        WHERE at >= :cut AND kind = 'page' AND NOT bot
        GROUP BY path ORDER BY views DESC LIMIT :lim
    """), {"cut": _window(days), "lim": limit}).fetchall()

    # `label` is added alongside `path`, never in place of it: the path is what
    # identifies the row and what a link has to point at.
    labels = _labels(db, [r[0] for r in rows])
    return {"pages": [{"path": r[0], "label": labels.get(r[0]),
                       "views": r[1], "visitors": r[2]} for r in rows]}


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
