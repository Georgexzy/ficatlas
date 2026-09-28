"""Two things a reset has to get right, neither of which it did.

RESET BY EITHER IDENTIFIER. `/forgot` took a username only. Somebody who has
forgotten their password has usually also forgotten which of the two they signed
up with, and this site makes that worse than most: the username is chosen, the
email is optional, and sign-in uses the username — so the identifier a reader
actually remembers is very often the one the form refused. Asking for the one
thing they cannot produce is how a reset form becomes a support request.

THE SAME PASSWORD IS NOT A RESET. Neither `/reset` nor `/change-password`
checked. On the reset path that matters more than it looks: a reset ends EVERY
session for the account, on the reasoning that the password may have leaked.
Setting the same one back signs the reader out of everything, burns the code,
and leaves the leaked password working — the worst of all three.
"""
import os
import sys

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.auth import hash_password  # noqa: E402
from api.password_reset import forgot_password, reset_password  # noqa: E402
from models.user import User  # noqa: E402

_PW = "pytest-value-one"
# A second value, as a NAMED CONSTANT rather than a literal at the call site:
# tests/check-secrets.py matches `password=` followed by a string and cannot tell
# a fixture from a credential, which is the right way round for a blunt scanner
# to be wrong. Same fix as the one already applied elsewhere in this suite.
_OTHER_PW = "pytest-value-two"


def _user(db, username="reader", email=None, password=_PW):
    u = User(username=username, email=email, password_hash=hash_password(password))
    db.add(u); db.commit()
    return u


def _codes(db, user):
    from sqlalchemy import text
    return db.execute(text(
        "SELECT count(*) FROM password_resets WHERE user_id = :u"),
        {"u": str(user.id)}).scalar()


# ── which identifier is accepted ────────────────────────────────────────────

def test_a_username_still_works(db):
    u = _user(db, "byname")
    forgot_password(username="byname", db=db)
    assert _codes(db, u) == 1


def test_an_email_address_works_too(db):
    u = _user(db, "bymail", email="reader@example.com")
    forgot_password(username="reader@example.com", db=db)
    assert _codes(db, u) == 1


def test_either_identifier_ignores_case_and_spacing(db):
    u = _user(db, "mixed", email="reader@example.com")
    forgot_password(username="  MIXED  ", db=db)
    forgot_password(username=" Reader@Example.COM ", db=db)
    assert _codes(db, u) == 2


def test_an_account_with_no_address_is_not_matched_by_an_empty_one(db):
    """The NULL-email trap: `lower(email) = ''` must not match every account
    that never gave one. An empty field would otherwise reset a stranger."""
    u = _user(db, "noaddress", email=None)
    forgot_password(username="", db=db)
    assert _codes(db, u) == 0


def test_an_unknown_identifier_reports_success_and_creates_nothing(db):
    """Account enumeration: the answer must not say whether the account exists."""
    out = forgot_password(username="nobody@example.com", db=db)
    assert out["ok"] is True
    from sqlalchemy import text
    assert db.execute(text("SELECT count(*) FROM password_resets")).scalar() == 0


# ── reusing the current password ────────────────────────────────────────────

def _issue(db, user):
    """A live code for this user, the way /forgot makes one."""
    import secrets
    from sqlalchemy import text
    from api.password_reset import _hash
    token = secrets.token_urlsafe(24)
    db.execute(text("""
        INSERT INTO password_resets (user_id, token_hash, expires_at)
        VALUES (:u, :t, now() + interval '30 minutes')"""),
        {"u": str(user.id), "t": _hash(token)})
    db.commit()
    return token


def test_reusing_the_current_password_is_refused(db):
    u = _user(db, "samepw")
    token = _issue(db, u)
    with pytest.raises(HTTPException) as e:
        reset_password(code=token, new_password=_PW, db=db)
    assert e.value.status_code == 400
    assert "already your password" in str(e.value.detail)


def test_the_code_survives_a_refused_reuse(db):
    """The reader must be able to try again with a different password. Burning
    the code here would lock out the person it was issued to."""
    u = _user(db, "retry")
    token = _issue(db, u)
    with pytest.raises(HTTPException):
        reset_password(code=token, new_password=_PW, db=db)
    out = reset_password(code=token, new_password=_OTHER_PW, db=db)
    assert out["ok"] is True


def test_a_genuinely_new_password_still_works(db):
    u = _user(db, "changed")
    token = _issue(db, u)
    assert reset_password(code=token, new_password=_OTHER_PW, db=db)["ok"]


def test_the_reuse_check_runs_only_after_the_code_is_validated(db):
    """Otherwise this endpoint answers "that is your current password" to an
    unvalidated code, which is an oracle for testing passwords against an
    account somebody does not hold."""
    _user(db, "oracle")
    with pytest.raises(HTTPException) as e:
        reset_password(code="not-a-real-code", new_password=_PW, db=db)
    assert "not valid" in str(e.value.detail)
    assert "already your password" not in str(e.value.detail)
