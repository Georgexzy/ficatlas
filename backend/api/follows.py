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

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
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


class EmailPrefs(BaseModel):
    enabled: bool
    frequency: str = "daily"


def _mask(email: str) -> str | None:
    """g•••@gmail.com — enough to recognise, not enough to read off a screen."""
    e = (email or "").strip()
    if "@" not in e:
        return None
    local, domain = e.split("@", 1)
    return f"{local[:1]}•••@{domain}"


def _email_state(db: Session, user: User) -> dict:
    import mailer
    row = db.execute(text("""
        SELECT frequency, last_sent_at FROM follow_email WHERE user_id = :u
    """), {"u": str(user.id)}).first()
    following = db.execute(text(
        "SELECT count(*) FROM follows WHERE user_id = :u"), {"u": str(user.id)}).scalar() or 0
    return {"enabled": bool(row), "frequency": row[0] if row else "daily",
            "has_email": bool((user.email or "").strip()),
            "email": _mask(user.email or ""),
            "last_sent_at": row[1].isoformat() if row and row[1] else None,
            "following": int(following),
            "mail_available": mailer.configured()}


# Declared BEFORE "/{story_id}": FastAPI matches in order, and "email" is a
# perfectly good story_id as far as a path parameter is concerned.
@router.get("/email")
def email_prefs(db: Session = Depends(get_db),
                viewer: Optional[User] = Depends(get_current_user)):
    """Whether this reader gets update mail. See follow_email.py."""
    return _email_state(db, _require(viewer))


@router.put("/email")
def set_email_prefs(prefs: EmailPrefs, db: Session = Depends(get_db),
                    viewer: Optional[User] = Depends(get_current_user)):
    user = _require(viewer)
    if prefs.frequency not in ("daily", "hourly"):
        raise HTTPException(422, "frequency must be daily or hourly")
    if not prefs.enabled:
        db.execute(text("DELETE FROM follow_email WHERE user_id = :u"), {"u": str(user.id)})
    else:
        if not (user.email or "").strip():
            raise HTTPException(400, "Add an email address to your account first.")
        # Turning it on must not mail the reader about everything that moved
        # before they asked: catch the mailed state up to now, so the first
        # digest is about chapters posted AFTER the box was ticked.
        db.execute(text("""
            UPDATE follows f SET mailed_chapters = s.chapter_count
              FROM stories s WHERE s.id = f.story_id AND f.user_id = :u
               AND NOT EXISTS (SELECT 1 FROM follow_email e WHERE e.user_id = :u)
        """), {"u": str(user.id)})
        db.execute(text("""
            INSERT INTO follow_email (user_id, frequency, token)
            VALUES (:u, :f, :t)
            ON CONFLICT (user_id) DO UPDATE SET frequency = EXCLUDED.frequency
        """), {"u": str(user.id), "f": prefs.frequency, "t": secrets.token_urlsafe(24)})
    db.commit()
    return _email_state(db, user)


# One test mail per account per ten minutes, per API process. Approximate
# across processes, which is fine for its purpose: a reader checking the mail
# arrives, not a way to send mail at volume.
_TEST_GAP_S = 600
_last_test: dict[str, float] = {}


@router.post("/email/test")
def send_test(db: Session = Depends(get_db),
              viewer: Optional[User] = Depends(get_current_user)):
    """Send this reader a sample update email, so they can see it arrives.

    The commonest question about an opt-in mail is "is it working?", asked by
    somebody whose followed works have not updated yet. This answers it now,
    with what a real digest of their own follows looks like.
    """
    import time as _time
    import follow_email
    import mailer
    user = _require(viewer)
    if not mailer.configured():
        raise HTTPException(503, "Email is not available on this site right now.")
    if not (user.email or "").strip():
        raise HTTPException(400, "Add an email address to your account first.")
    now = _time.time()
    if now - _last_test.get(str(user.id), 0) < _TEST_GAP_S:
        raise HTTPException(429, "A test email was sent a few minutes ago — check your inbox, and your spam folder.")
    rows = db.execute(text("""
        SELECT s.id::text, s.title, s.author, s.site::text, s.chapter_count, 1
          FROM follows f JOIN stories s ON s.id = f.story_id
         WHERE f.user_id = :u AND s.delisted_at IS NULL
         ORDER BY s.updated_at DESC NULLS LAST LIMIT 3
    """), {"u": str(user.id)}).fetchall()
    token = db.execute(text("SELECT token FROM follow_email WHERE user_id = :u"),
                       {"u": str(user.id)}).scalar() or "test"
    if rows:
        subject, body, headers = follow_email.compose(user.username, list(rows), token)
        subject = "[Test] " + subject
        body = ("This is a TEST of your FicAtlas update emails. Nothing below\n"
                "has necessarily updated — it shows what a real one looks like.\n\n" + body)
    else:
        subject = "[Test] Your FicAtlas update emails work"
        body = (f"Hi {user.username},\n\nThis is a test. Update emails reach this address.\n"
                "Follow a story and you will hear when it gains chapters.\n")
        headers = {}
    if not mailer.send(user.email, subject, body, headers):
        raise HTTPException(502, "The email could not be sent. Try again later.")
    _last_test[str(user.id)] = now
    return {"ok": True, "to": _mask(user.email)}


@router.post("/email/unsubscribe")
async def unsubscribe(request: Request, db: Session = Depends(get_db)):
    """Stop update mail, by the token in the mail. No session needed.

    POST only. Mail scanners and link previewers GET every link in a message,
    and an unsubscribe that fires on GET switches readers off without their
    knowing. Mail clients' own button POSTs here (RFC 8058, the token in the
    query string), and the /unsubscribe page POSTs it as JSON from the fragment.
    """
    token = request.query_params.get("t")
    if not token and "json" in (request.headers.get("content-type") or ""):
        try:
            token = (await request.json()).get("t")
        except Exception:
            token = None
    if not token:
        raise HTTPException(400, "Missing token.")
    gone = db.execute(text("DELETE FROM follow_email WHERE token = :t"), {"t": token}).rowcount
    db.commit()
    # The same answer either way: a second click on an old link is not an
    # error, and the token says nothing about whose it was.
    return {"ok": True, "unsubscribed": bool(gone)}


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
