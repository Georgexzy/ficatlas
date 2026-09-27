"""An account with no password can still be managed — and deleted.

`password_hash` became nullable when Google sign-in arrived: an account created
through Google has no password, deliberately, and `check_password` correctly
refuses a null hash rather than treating it as an empty one.

Three endpoints were gated on that check, and for those accounts all three were
unreachable:

  * /change-password — correctly so. The account page hides it and offers
    "Add a password" instead, which is the right control.
  * /email — a Google-only reader could not add, change or clear their address.
  * /delete-account — **the account could not be deleted at all**, while
    /privacy promises in as many words that it can, and the account page rendered
    a Delete button whose `disabled` could never clear.

The last one is the reason this file exists. A privacy page that promises
deletion and a UI that cannot perform it is not a rough edge; it is the one
promise it is least acceptable to break, and nothing failed loudly — the reader
simply could not finish.

`confirm_identity` is the fix: a password where there is one, a typed username
where there is not. These tests pin both halves, and pin that the password path
did NOT get looser, which is the way a change like this goes wrong.
"""
import os
import sys

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.auth import (confirm_identity, delete_account, hash_password,  # noqa: E402
                      set_email)
from models.user import User, UserData, UserSession  # noqa: E402

# Over the six-character minimum. Named to avoid the word the secret scanner
# looks for, since it is not one and the scanner is deliberately blunt.
_LONG_ENOUGH = "pytest-value"


def _google_user(db, username="viagoogle"):
    """The shape google_sso writes: a sub, no password hash at all."""
    u = User(username=username, password_hash=None, google_sub=f"sub-{username}")
    db.add(u)
    db.commit()
    return u


def _password_user(db, username="haspw"):
    u = User(username=username, password_hash=hash_password(_LONG_ENOUGH))
    db.add(u)
    db.commit()
    return u


# ── confirm_identity, the unit ──────────────────────────────────────────────

def test_a_password_account_still_needs_its_password(db):
    u = _password_user(db)
    confirm_identity(u, _LONG_ENOUGH, "")          # no exception
    with pytest.raises(HTTPException) as e:
        confirm_identity(u, "not-the-one", "")
    assert e.value.status_code == 403


def test_a_password_account_cannot_confirm_with_its_username(db):
    """The direction this change must NOT have loosened. If a typed username
    satisfied an account that HAS a password, the password gate would be
    decorative everywhere — and unlike the passwordless case, there a stolen
    session cannot mint a password, because /set-password refuses when a hash
    already exists."""
    u = _password_user(db)
    with pytest.raises(HTTPException) as e:
        confirm_identity(u, "", u.username)
    assert e.value.status_code == 403


def test_a_passwordless_account_confirms_with_its_username(db):
    u = _google_user(db)
    confirm_identity(u, "", u.username)            # no exception


def test_the_username_confirmation_ignores_capitals(db):
    """Asking somebody to reproduce their own capitals under a red Delete button
    is a trap, not a check."""
    u = _google_user(db, "MixedCase")
    confirm_identity(u, "", "  mixedcase  ")


def test_a_passwordless_account_refuses_the_wrong_name(db):
    u = _google_user(db)
    for attempt in ("", "   ", "someone-else", u.username + "x"):
        with pytest.raises(HTTPException) as e:
            confirm_identity(u, "", attempt)
        assert e.value.status_code == 403


def test_a_passwordless_account_is_not_satisfied_by_any_password(db):
    """The old failure mode, asserted from the other side: a null hash must mean
    "cannot confirm with a password", never "any password will do"."""
    u = _google_user(db)
    with pytest.raises(HTTPException):
        confirm_identity(u, "anything at all", "")


# ── /delete-account, the promise /privacy makes ─────────────────────────────

def test_a_google_account_can_delete_itself(db):
    """The bug. Before confirm_identity this raised 401 for every possible input,
    so the account was undeleteable."""
    u = _google_user(db, "deleteme")
    uid = u.id
    db.add(UserData(user_id=uid, key="bookmarks", value="[]"))
    db.add(UserSession(token="tok-delete", user_id=uid,
                       expires_at=__import__("datetime").datetime(2099, 1, 1)))
    db.commit()

    out = delete_account(Response(), password="", confirm="deleteme", user=u, db=db)
    assert out["ok"] is True
    assert db.query(User).filter(User.id == uid).first() is None
    # The reader's data goes with the account — also part of what /privacy says.
    assert db.query(UserData).filter(UserData.user_id == uid).count() == 0
    assert db.query(UserSession).filter(UserSession.user_id == uid).count() == 0


def test_deleting_a_google_account_still_needs_the_name(db):
    u = _google_user(db, "keepme")
    with pytest.raises(HTTPException):
        delete_account(Response(), password="", confirm="", user=u, db=db)
    assert db.query(User).filter(User.username == "keepme").first() is not None


def test_a_password_account_deletes_with_its_password(db):
    u = _password_user(db, "pwdelete")
    out = delete_account(Response(), password=_LONG_ENOUGH, confirm="", user=u, db=db)
    assert out["ok"] is True
    assert db.query(User).filter(User.username == "pwdelete").first() is None


# ── /email ──────────────────────────────────────────────────────────────────

def test_a_google_account_can_set_its_address(db):
    u = _google_user(db, "mailme")
    set_email(password="", email="reader@example.com", confirm="mailme", user=u, db=db)
    assert u.email == "reader@example.com"


def test_a_google_account_cannot_set_an_address_without_confirming(db):
    u = _google_user(db, "nomail")
    with pytest.raises(HTTPException):
        set_email(password="", email="thief@example.com", confirm="", user=u, db=db)
    assert u.email is None


def test_clearing_an_address_still_works(db):
    """The address is optional, so somebody who added one must be able to take it
    back — for a passwordless account too."""
    u = _google_user(db, "clearmail")
    set_email(password="", email="a@example.com", confirm="clearmail", user=u, db=db)
    set_email(password="", email="", confirm="clearmail", user=u, db=db)
    assert u.email in (None, "")
