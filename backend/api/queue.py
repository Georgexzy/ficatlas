"""The fic-finder posts waiting for an answer.

Read side of reddit_queue.py — see that module for why the queue exists and why
it is fetched as slowly as it is. This only serves what was stored and lets a
person mark a post done.

ADMIN, not owner. The traffic reports are owner-gated because they are a record
of the audience's own behaviour; this is a list of public Reddit posts and the
searches they produce, which is the same class of thing as running an import.

It posts NOTHING to Reddit and holds no credential that could. The reply is
still written in the outreach panel, copied by a person, and sent by a person.
"""
import logging

import time

from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException,
                     Query)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import require_admin
from db.session import get_db

log = logging.getLogger(__name__)
router = APIRouter()

_STATES = ("new", "answered", "skipped")


class QueuedPost(BaseModel):
    id: str
    subreddit: str
    title: str
    body: str | None = None
    url: str
    posted_at: str | None = None
    # What the extractor made of the post, and how many works that finds.
    # `works` is a probe count and therefore a floor, which is all the
    # ordering needs — see reddit_queue.
    query: str | None = None
    works: int | None = None
    link_unsafe: bool = False
    state: str = "new"
    # Which feed it came from. See the ordering below: this is the difference
    # between a post this index can answer and one it cannot.
    flair: str | None = None


# One fetch at a time, and a floor between them. The feed is unauthenticated
# and Reddit 429s the second request from an address, so a button that could be
# pressed twice is a button that produces nothing twice — and being a bad guest
# on somebody else's rate limit is how the whole channel gets closed.
_REFRESH_MIN_GAP = 120.0
_last_refresh = 0.0
_refreshing = False


@router.post("/refresh")
def refresh(background: BackgroundTasks, _admin=Depends(require_admin)):
    """Fetch now, rather than waiting for the hourly run.

    Returns immediately and does the work behind the response. A run walks the
    feeds with a twenty-second gap between them, so holding the request open
    would mean a two-minute spinner for something the panel can simply pick up
    on its next load.
    """
    global _last_refresh, _refreshing
    now = time.monotonic()
    if _refreshing:
        return {"started": False, "reason": "already fetching"}
    if now - _last_refresh < _REFRESH_MIN_GAP:
        wait = int(_REFRESH_MIN_GAP - (now - _last_refresh))
        return {"started": False, "reason": f"just fetched — try again in {wait}s"}
    _last_refresh = now

    def _go():
        global _refreshing
        _refreshing = True
        try:
            import reddit_queue
            reddit_queue.run()
        except Exception:
            log.warning("manual reddit fetch failed", exc_info=True)
        finally:
            _refreshing = False

    background.add_task(_go)
    return {"started": True}


@router.get("/counts")
def counts(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """How many are waiting, by state and by subreddit.

    Its own endpoint so the tab can carry a number without loading the list —
    a worklist whose size is only visible once you open it is one you forget to
    open.
    """
    try:
        by_state = {r[0]: r[1] for r in db.execute(text(
            "SELECT state, count(*) FROM reddit_posts GROUP BY 1")).fetchall()}
        subs = [{"subreddit": r[0], "waiting": r[1]} for r in db.execute(text(
            "SELECT subreddit, count(*) FROM reddit_posts WHERE state='new' "
            "GROUP BY 1 ORDER BY 2 DESC")).fetchall()]
    except Exception:
        log.debug("reddit_posts unavailable", exc_info=True)
        return {"states": {}, "subreddits": []}
    return {"states": by_state, "subreddits": subs}


@router.get("", response_model=list[QueuedPost])
def list_queue(state: str = Query("new"),
               subreddit: str = Query(""),
               flair: str = Query(""),
               order: str = Query("answerable"),
               search: str = Query(""),
               limit: int = Query(40, ge=1, le=200),
               db: Session = Depends(get_db),
               _admin=Depends(require_admin)):
    """Posts we can answer first, then the rest.

    Ordered by whether there is an answer at all, then by how few works it
    takes to give it: a post that resolves to four works is a better use of a
    person's next ten minutes than one that resolves to five thousand, because
    the four can be read and the five thousand cannot.

    Posts whose reply would carry a link the subreddit's rules forbid sort
    last and are labelled, rather than being hidden — the reason is more useful
    than the absence, and a person may still want to answer in words.
    """
    if state not in _STATES:
        raise HTTPException(status_code=400, detail="unknown state")
    # Two orderings, because they answer different questions. ANSWERABLE is the
    # default and the useful one — what can I do something about right now.
    # NEWEST is for when a thread is live and being answered by other people,
    # where arriving late is the same as not arriving.
    # RECS FIRST, and it is not a preference — it is which posts this index can
    # actually answer.
    #
    # A "Recs Wanted" post is a FILTER: "shipping fics from the POV of an
    # angsty character", "arranged marriage, no harem". Measured on 25 of
    # them: 23 produced an answer and 18 of those were TIGHT — under 400 works,
    # which is a reply somebody can read.
    #
    # A "Lost Fic" post is an IDENTIFICATION of one specific remembered work,
    # and the detail that would identify it — a scene, a line, a cover — is
    # not in anybody's metadata. The best possible extraction still answers
    # "Harry Potter, time travel, 5,000 works", and that is not the fic. A
    # tight number there is not a better answer, it is a narrower guess.
    #
    # So the flair leads the ordering and the rest breaks ties. Lost Fic posts
    # are still fetched and still shown — a narrowed search is a useful reply
    # and costs nothing — they simply stop outranking posts that can be
    # answered properly.
    _recs_first = ("(COALESCE(flair,'') ILIKE '%rec%') DESC, ")
    order_sql = {
        "answerable": (_recs_first
                       + "link_unsafe ASC, (works IS NULL OR works = 0) ASC, "
                       "works ASC NULLS LAST, posted_at DESC NULLS LAST"),
        "newest": _recs_first + "posted_at DESC NULLS LAST",
        # For when you want to work the identification posts deliberately.
        "oldest_first": "posted_at ASC NULLS LAST",
    }.get(order)
    if not order_sql:
        raise HTTPException(status_code=400, detail="unknown order")
    try:
        rows = db.execute(text(f"""
            SELECT id, subreddit, title, body, url, posted_at,
                   query, works, link_unsafe, state, flair
              FROM reddit_posts
             WHERE state = :s
               AND (:sub = '' OR subreddit = :sub)
               AND (:flair = '' OR COALESCE(flair,'') ILIKE '%' || :flair || '%')
               AND (:q = '' OR title ILIKE '%%' || :q || '%%'
                            OR body  ILIKE '%%' || :q || '%%')
             ORDER BY {order_sql}
             LIMIT :lim
        """), {"s": state, "sub": subreddit, "q": search.strip(),
               "flair": flair.strip(), "lim": limit}).fetchall()
    except Exception:
        # The table is built offline and does not exist until the first run.
        # An empty queue is a queue; a 500 is a broken panel.
        log.debug("reddit_posts unavailable", exc_info=True)
        return []
    return [QueuedPost(
        id=r[0], subreddit=r[1], title=r[2], body=r[3], url=r[4],
        posted_at=r[5].isoformat() if r[5] else None,
        query=r[6], works=r[7], link_unsafe=bool(r[8]), state=r[9],
        flair=r[10])
        for r in rows]


class StateChange(BaseModel):
    state: str


@router.post("/{post_id:path}/state")
def set_state(post_id: str, change: StateChange,
              db: Session = Depends(get_db),
              _admin=Depends(require_admin)):
    """Mark one post answered or skipped.

    Both are the same operation to this endpoint and deliberately so: the
    difference is a note to the next person opening the list, not a difference
    in what happens. Nothing is sent either way.
    """
    if change.state not in _STATES:
        raise HTTPException(status_code=400, detail="unknown state")
    db.execute(text("UPDATE reddit_posts SET state = :s WHERE id = :id"),
               {"s": change.state, "id": post_id})
    db.commit()
    return {"ok": True, "state": change.state}
