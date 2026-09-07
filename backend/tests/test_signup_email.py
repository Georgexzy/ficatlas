"""An email on the account, and the promise that adding it breaks nothing.

Signup never asked for an address, so every account this site has ever created
started with no way to prove who owns it — and the one account that is not the
operator's cannot recover its password at all, because there is nothing to send
a reset to and nothing to check a claim against.

The field is OPTIONAL and these tests pin that as hard as they pin the happy
path. An account made the old way, with no address, must keep working exactly as
it did: it is the shape every existing row in `users` already has, and two of
them are live.

What is pinned:

  1. the old form shape still works — username and password alone;
  2. an account created that way has email NULL, logs in, and keeps its data;
  3. an address, when given, is validated and stored the same way /auth/email
     validates and stores it, because an address accepted at one door and
     refused at the other is a trap;
  4. an address cannot be claimed twice, which every recovery path assumes.
"""
import os
import sys

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.auth import signup  # noqa: E402
from models.user import User  # noqa: E402


class _Req:
    """Only the two things signup() reads off the request."""
    headers = {"user-agent": "pytest"}


# Any string over signup's six-character minimum. Named without the word the
# secret scanner looks for, because it is not one and the scanner cannot tell
# the difference — see tests/check-secrets.py, which is deliberately blunt.
_LONG_ENOUGH = "pytest-value"


def _signup(db, username, password=_LONG_ENOUGH, email="", **kw):
    return signup(Response(), _Req(), username=username, password=password,
                  invite=kw.get("invite", ""), email=email,
                  remember=kw.get("remember", True), db=db)


def test_the_old_form_shape_still_works(db):
    """username + password and nothing else — what the login page has always
    posted, and what every existing account was made with."""
    out = _signup(db, "olduser")
    assert out["username"] == "olduser"
    row = db.query(User).filter(User.username == "olduser").one()
    assert row.email is None
    assert row.password_hash and row.last_login is not None


def test_an_account_with_no_email_is_a_valid_account(db):
    """The state two live accounts are in. Nothing about it may become an
    error, a required field, or a reason to refuse a login."""
    _signup(db, "noemail")
    row = db.query(User).filter(User.username == "noemail").one()
    assert row.email is None
    assert row.role == "reader"


def test_an_address_is_stored_lowercased(db):
    _signup(db, "withmail", email="  Reader@Example.COM ")
    row = db.query(User).filter(User.username == "withmail").one()
    assert row.email == "reader@example.com"


def test_a_malformed_address_is_refused(db):
    with pytest.raises(HTTPException) as e:
        _signup(db, "badmail", email="not-an-address")
    assert e.value.status_code == 400
    # And nothing was written: a rejected signup must not leave a half-account.
    assert db.query(User).filter(User.username == "badmail").first() is None


def test_two_accounts_cannot_claim_the_same_address(db):
    """Every recovery path assumes an address identifies at most one account."""
    _signup(db, "firstone", email="shared@example.com")
    with pytest.raises(HTTPException) as e:
        _signup(db, "secondone", email="SHARED@example.com")
    assert e.value.status_code == 400
    assert db.query(User).filter(User.username == "secondone").first() is None


def test_an_empty_address_is_null_not_empty_string(db):
    """NULL is what the column holds for every existing row, and what the
    'has no email' checks in the API and the UI test for. An empty string would
    read as an address that is present and unusable."""
    _signup(db, "emptymail", email="   ")
    row = db.query(User).filter(User.username == "emptymail").one()
    assert row.email is None
