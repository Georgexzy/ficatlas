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

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("", response_model=list[QueuedPost])
def list_queue(state: str = Query("new"),
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
    try:
        rows = db.execute(text("""
            SELECT id, subreddit, title, body, url, posted_at,
                   query, works, link_unsafe, state
              FROM reddit_posts
             WHERE state = :s
             ORDER BY link_unsafe ASC,
                      (works IS NULL OR works = 0) ASC,
                      works ASC NULLS LAST,
                      posted_at DESC NULLS LAST
             LIMIT :lim
        """), {"s": state, "lim": limit}).fetchall()
    except Exception:
        # The table is built offline and does not exist until the first run.
        # An empty queue is a queue; a 500 is a broken panel.
        log.debug("reddit_posts unavailable", exc_info=True)
        return []
    return [QueuedPost(
        id=r[0], subreddit=r[1], title=r[2], body=r[3], url=r[4],
        posted_at=r[5].isoformat() if r[5] else None,
        query=r[6], works=r[7], link_unsafe=bool(r[8]), state=r[9])
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
