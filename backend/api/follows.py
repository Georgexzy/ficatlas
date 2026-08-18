"""Following a work, and being told when it changes.

Why this exists here rather than on the archives
------------------------------------------------
Every archive offers subscriptions and none of them can offer the thing a reader
of this index actually wants: one list. Someone following a WIP on AO3, a
long-running story on FanFiction.net and a finished series on FictionAlley currently
keeps three sets of subscriptions in three places, two of which email them. This
is the one feature a cross-archive index can do that no single archive can.

Why there is no notification queue
----------------------------------
An update is a COMPARISON, not an event. The follow row records what the reader
had seen; whether a work has moved since is answered by looking at the story row
at read time.

That is deliberate, and it is the difference between a feature that works and
one that quietly rots:

  * a work is flagged correctly whichever path updated it — live fetch, the
    listing harvest, the Wayback harvest, a bulk import. An event-based design
    would need every one of those to remember to fan out, and the failure mode
    of forgetting is silent.
  * there is no queue to drain, retry, deduplicate or back up, and no way for a
    missed event to leave a follow permanently stale.
  * it costs one indexed join. Reading it is a page load, not a cron job.

The cost is that "new" means "new since you last looked", not "new since a
notification was sent" — which is what a reader means anyway.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import get_current_user
from db.session import get_db
from models.user import User

router = APIRouter()


class FollowedWork(BaseModel):
    id: str
    title: str
    author: Optional[str] = None
    site: Optional[str] = None
    chapter_count: int
    word_count: Optional[int] = None
    status: Optional[str] = None
    updated_at: Optional[str] = None
    # What changed since the reader last looked.
    new_chapters: int
    is_new: bool


def _require(viewer: Optional[User]) -> User:
    if viewer is None:
        raise HTTPException(401, "Sign in to follow stories.")
    return viewer


@router.get("", response_model=list[FollowedWork])
def list_follows(db: Session = Depends(get_db),
                 viewer: Optional[User] = Depends(get_current_user)):
    """Everything this reader follows, works with updates first.

    Delisted works are excluded: a story whose author asked to be removed should
    not keep appearing in someone's list, and there is nothing to link them to.
    """
    user = _require(viewer)
    rows = db.execute(text("""
        SELECT s.id::text, s.title, s.author, s.site, s.chapter_count,
               s.word_count, s.status, s.updated_at,
               f.seen_chapters, f.seen_updated
          FROM follows f
          JOIN stories s ON s.id = f.story_id
         WHERE f.user_id = :u
           AND s.delisted_at IS NULL
         ORDER BY (s.chapter_count > f.seen_chapters) DESC,
                  s.updated_at DESC NULLS LAST
    """), {"u": str(user.id)}).fetchall()

    out = []
    for r in rows:
        chapters = r[4] or 0
        seen = r[8] or 0
        # Clamped at zero: a work can LOSE chapters (an author unpublishing, or
        # a metadata correction like the digit-concatenation repair), and
        # "-3 new chapters" is not a thing to show anybody.
        new_chapters = max(0, chapters - seen)
        moved = bool(r[7] and r[9] and r[7] > r[9])
        out.append(FollowedWork(
            id=r[0], title=r[1], author=r[2], site=r[3],
            chapter_count=chapters, word_count=r[5],
            status=str(r[6]) if r[6] is not None else None,
            updated_at=r[7].isoformat() if r[7] else None,
            new_chapters=new_chapters,
            is_new=new_chapters > 0 or moved,
        ))
    return out


@router.get("/count")
def unread_count(db: Session = Depends(get_db),
                 viewer: Optional[User] = Depends(get_current_user)):
    """How many followed works have moved. Drives the badge.

    Answers 0 for a signed-out reader rather than 401: this is called on every
    page to decide whether to show a badge, and a 401 there would be noise in
    the console for the ordinary case of not being signed in.
    """
    if viewer is None:
        return {"unread": 0, "following": 0}
    row = db.execute(text("""
        SELECT count(*) FILTER (WHERE s.chapter_count > f.seen_chapters
                                   OR (s.updated_at IS NOT NULL
                                       AND f.seen_updated IS NOT NULL
                                       AND s.updated_at > f.seen_updated)),
               count(*)
          FROM follows f
          JOIN stories s ON s.id = f.story_id
         WHERE f.user_id = :u AND s.delisted_at IS NULL
    """), {"u": str(viewer.id)}).first()
    return {"unread": row[0] or 0, "following": row[1] or 0}


@router.get("/{story_id}")
def follow_state(story_id: str, db: Session = Depends(get_db),
                 viewer: Optional[User] = Depends(get_current_user)):
    """Whether this reader follows this work — for the button's initial state.

    `signed_in` is reported separately from `following`, because the caller needs
    to tell "not following" from "cannot follow". Answering a plain
    {"following": false} for a signed-out visitor made the button render for
    them, and it would have 401'd on the first click — a control that appears and
    then fails teaches people the app is broken rather than that it is not
    theirs. 200 rather than 401 is still right: this is called on every story
    page and being signed out is not an error.
    """
    if viewer is None:
        return {"following": False, "signed_in": False}
    found = db.execute(text(
        "SELECT 1 FROM follows WHERE user_id = :u AND story_id = :s"
    ), {"u": str(viewer.id), "s": story_id}).first()
    return {"following": bool(found), "signed_in": True}


@router.post("/{story_id}")
def follow(story_id: str, db: Session = Depends(get_db),
           viewer: Optional[User] = Depends(get_current_user)):
    """Start following, recording the work's current state as already seen.

    Following is not a claim that you have read it — but it IS a claim that you
    do not want to be told about what is already there, or every new follow
    would arrive with its whole back catalogue marked unread.
    """
    user = _require(viewer)
    story = db.execute(text(
        "SELECT chapter_count, updated_at FROM stories "
        " WHERE id = :s AND delisted_at IS NULL"
    ), {"s": story_id}).first()
    if not story:
        raise HTTPException(404, "No such story.")

    db.execute(text("""
        INSERT INTO follows (user_id, story_id, seen_chapters, seen_updated)
        VALUES (:u, :s, :c, :t)
        ON CONFLICT (user_id, story_id) DO NOTHING
    """), {"u": str(user.id), "s": story_id,
           "c": story[0] or 0, "t": story[1]})
    db.commit()
    return {"following": True}


@router.delete("/{story_id}")
def unfollow(story_id: str, db: Session = Depends(get_db),
             viewer: Optional[User] = Depends(get_current_user)):
    user = _require(viewer)
    db.execute(text("DELETE FROM follows WHERE user_id = :u AND story_id = :s"),
               {"u": str(user.id), "s": story_id})
    db.commit()
    return {"following": False}


@router.post("/{story_id}/seen")
def mark_seen(story_id: str, db: Session = Depends(get_db),
              viewer: Optional[User] = Depends(get_current_user)):
    """Catch this follow up to where the work is now."""
    user = _require(viewer)
    updated = db.execute(text("""
        UPDATE follows f
           SET seen_chapters = s.chapter_count, seen_updated = s.updated_at
          FROM stories s
         WHERE s.id = f.story_id AND f.user_id = :u AND f.story_id = :s
    """), {"u": str(user.id), "s": story_id}).rowcount
    db.commit()
    if not updated:
        raise HTTPException(404, "You are not following that story.")
    return {"ok": True}
