"""Staying signed in, and the three ways it silently stopped being true.

"Stay signed in on this device" is a promise made at the login form, and every
bug here broke it somewhere the reader could not see: not at the moment they
ticked the box, but days later, on a page load that just said "Sign in" again.

Three things are pinned:

  1. the cookie's shape follows the box — Max-Age when ticked, none when not;
  2. a remembered session ROLLS ITS COOKIE FORWARD as it is used, so the 90 days
     run from the last visit rather than from the login;
  3. converting a pre-hash session token keeps everything about it, including
     the answer to the box, and can be done twice without destroying it.

And one that is not about cookies at all: previewing the site as a lesser role
must never write that demotion to the account. There is one owner seat on this
instance, and the version of this code that assigned to `user.role` would have
given it away to whichever request happened to commit next.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

from fastapi import Response
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.auth import (SESSION_DAYS, _set_session_cookie, _token_hash,
                      get_current_user, me)
from models.user import ROLE_OWNER, User


def _user(db, role="reader") -> User:
    uid = db.execute(text("""
        INSERT INTO users (username, password_hash, role)
        VALUES (:n, 'x', :r) RETURNING id
    """), {"n": f"u_{uuid.uuid4().hex[:10]}", "r": role}).scalar()
    db.commit()
    return db.get(User, uid)


def _session(db, user, *, remember=True, hashed=True, view_as=None,
             last_used_ago=timedelta(0), expires_in=timedelta(days=SESSION_DAYS)):
    """Put a session row in the table and return the token the browser holds."""
    token = f"tok_{uuid.uuid4().hex}"
    stored = _token_hash(token) if hashed else token
    db.execute(text("""
        INSERT INTO user_sessions
            (token, user_id, created_at, expires_at, last_used, user_agent,
             remember, view_as)
        VALUES (:t, :u, now(), :exp, :used, 'test', :rem, :va)
    """), {"t": stored, "u": user.id,
           "exp": datetime.utcnow() + expires_in,
           "used": datetime.utcnow() - last_used_ago,
           "rem": remember, "va": view_as})
    db.commit()
    return token


# ── the shape of the cookie ─────────────────────────────────────────────────

def test_ticked_box_is_a_persistent_cookie():
    r = Response()
    _set_session_cookie(r, "abc", True)
    sc = r.headers.get("set-cookie")
    assert f"Max-Age={SESSION_DAYS * 86400}" in sc
    assert "HttpOnly" in sc and "Path=/" in sc


def test_unticked_box_is_a_session_cookie():
    r = Response()
    _set_session_cookie(r, "abc", False)
    sc = r.headers.get("set-cookie")
    # The whole mechanism: no Max-Age means the browser drops it on close.
    assert "Max-Age" not in sc
    assert "abc" in sc


def test_a_session_cookie_response_is_never_shared_cached():
    """A Set-Cookie on a response an edge cache may keep hands one reader's
    credential to the next one."""
    r = Response()
    _set_session_cookie(r, "abc", True)
    assert r.headers["Cache-Control"] == "private, no-store"


# ── rolling forward ─────────────────────────────────────────────────────────

def test_a_remembered_session_re_issues_its_cookie_when_used(db):
    """The 90 days should run from the last visit, not from the login.

    Before, the cookie was re-sent only once the ROW was inside its final third.
    Miss that one window — a dropped response, a device asleep, an offline
    spell — and the cookie expired on the day it was always going to, however
    much the site had been used since.
    """
    u = _user(db)
    tok = _session(db, u, remember=True, last_used_ago=timedelta(hours=1))
    r = Response()
    assert get_current_user(r, tok, db) is not None
    sc = r.headers.get("set-cookie")
    assert sc and f"Max-Age={SESSION_DAYS * 86400}" in sc


def test_an_unremembered_session_is_never_re_issued_persistent(db):
    """The failure this guards is the worst-timed one there is: handing a
    persistent cookie to somebody who ticked the box OFF, on a shared computer,
    silently."""
    u = _user(db)
    tok = _session(db, u, remember=False, last_used_ago=timedelta(hours=1))
    r = Response()
    assert get_current_user(r, tok, db) is not None
    assert "Max-Age" not in (r.headers.get("set-cookie") or "")


def test_a_freshly_used_session_does_not_re_issue_on_every_request(db):
    """One Set-Cookie per quarter hour, not one per request."""
    u = _user(db)
    tok = _session(db, u, remember=True, last_used_ago=timedelta(seconds=5))
    r = Response()
    assert get_current_user(r, tok, db) is not None
    assert r.headers.get("set-cookie") is None


def test_an_expired_session_is_refused_and_removed(db):
    u = _user(db)
    tok = _session(db, u, expires_in=timedelta(days=-1))
    r = Response()
    assert get_current_user(r, tok, db) is None
    assert db.execute(text("SELECT count(*) FROM user_sessions")).scalar() == 0


# ── converting a pre-hash token ─────────────────────────────────────────────

def test_legacy_token_converts_and_keeps_what_the_session_asked_for(db):
    """The conversion used to re-list the columns to carry over and forgot
    `remember`, so an unrelated deploy turned "forget me" into a 90-day cookie.
    An UPDATE of the key carries every column by construction."""
    u = _user(db)
    tok = _session(db, u, hashed=False, remember=False, view_as="reader",
                   last_used_ago=timedelta(hours=1))
    r = Response()
    assert get_current_user(r, tok, db) is not None

    row = db.execute(text("""
        SELECT token, remember, view_as FROM user_sessions
    """)).first()
    assert row[0] == _token_hash(tok), "token should now be stored hashed"
    assert row[1] is False, "the unticked box must survive the conversion"
    assert row[2] == "reader"
    assert "Max-Age" not in (r.headers.get("set-cookie") or "")


def test_converting_the_same_legacy_token_twice_does_not_destroy_it(db):
    """A page load makes several API calls at once, all carrying the same
    legacy cookie. The delete-then-insert version had them racing to insert the
    same primary key; the loser died with an IntegrityError, which the app reads
    as "the server is unwell" and shows as signed-in-but-nothing-works."""
    u = _user(db)
    tok = _session(db, u, hashed=False)
    for _ in range(3):
        assert get_current_user(Response(), tok, db) is not None
    assert db.execute(text("SELECT count(*) FROM user_sessions")).scalar() == 1


def test_an_unknown_token_is_just_not_signed_in(db):
    _user(db)
    assert get_current_user(Response(), "nonsense", db) is None


# ── role preview must not be written down ───────────────────────────────────

def test_previewing_a_lesser_role_never_demotes_the_account(db):
    """`role` is a mapped column. Assigning the preview to it marked the row
    dirty, and the next commit in the same request — a bookmark merge, a follow,
    the last_used refresh two lines above — flushed the demotion to the database
    for good. On an instance with one owner, that is the owner seat."""
    owner = _user(db, role=ROLE_OWNER)
    tok = _session(db, owner, view_as="reader", last_used_ago=timedelta(hours=1))

    user = get_current_user(Response(), tok, db)
    assert user is not None
    # The preview applies to this request...
    assert user.at_least(ROLE_OWNER) is False
    assert me(user)["user"]["role"] == "reader"
    assert me(user)["user"]["previewing"] is True

    # ...and anything that writes during it must not carry the demotion along.
    db.commit()
    db.expire_all()
    assert db.execute(text("SELECT role FROM users WHERE id = :i"),
                      {"i": owner.id}).scalar() == ROLE_OWNER


def test_preview_cannot_raise_a_role(db):
    """Enforced at read time as well as at write time, so neither is the only
    thing standing between a session row and an escalation."""
    reader = _user(db, role="reader")
    tok = _session(db, reader, view_as=ROLE_OWNER)
    user = get_current_user(Response(), tok, db)
    assert user.at_least(ROLE_OWNER) is False
    assert user.effective_role() == "reader"
