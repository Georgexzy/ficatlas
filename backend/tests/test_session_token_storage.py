"""Stored session tokens must never be usable as cookies.

A session token is a bearer credential: whoever holds it IS the user, with no
second factor. `_token_hash` was introduced so that `user_sessions` stops being
a list of working credentials -- and the reason that matters here rather than in
the abstract is that `backup.sh essential` dumps `user_sessions` every night and
`backup-offsite.sh` copies the dump to a laptop.

Hashing the column closed the hole for NEW sessions. Rows written before it went
in were left verbatim, so the table still contained live plaintext credentials
long afterwards -- the code was fixed and the data was not. These cover both
halves: that a legacy cookie keeps working once its stored row is hashed, and
that a value read out of the table cannot be replayed as a cookie.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.auth import _token_hash, _token_lookups, _LOOKS_STORED, _new_token


def test_a_stored_value_is_not_accepted_as_a_cookie():
    """The replay this exists to stop: read the column, paste it as `sat`.

    The legacy branch compares the cookie against the stored column as-is, and
    a stored hash matches itself -- so without this guard, read access to the
    table was read access to every live session.
    """
    stored = _token_hash(_new_token())
    assert _LOOKS_STORED.fullmatch(stored)
    assert stored not in _token_lookups(stored)


def test_a_real_cookie_is_still_tried_verbatim():
    """Genuine legacy tokens are urlsafe-base64 and effectively never all-hex,
    so refusing hash-shaped cookies does not lock anybody out."""
    legacy = _new_token()
    assert not _LOOKS_STORED.fullmatch(legacy)
    assert legacy in _token_lookups(legacy)


def test_hashing_a_legacy_row_keeps_that_session_working():
    """The migration rewrites the STORED value; the reader still holds the
    plaintext. Authentication has to survive that, or hashing the table logs
    everybody out."""
    cookie = _new_token()          # what the browser holds, unchanged
    migrated_row = _token_hash(cookie)   # what the row becomes
    assert migrated_row in _token_lookups(cookie)


def test_the_migration_is_idempotent():
    """Running it twice must not double-hash and invalidate every session."""
    import hash_legacy_sessions as m
    already = _token_hash(_new_token())
    assert m.needs_hashing(already) is False
    assert m.needs_hashing(_new_token()) is True


# ── brute-force lockout under more than one worker ───────────────────────────

def test_the_username_lockout_accounts_for_worker_count():
    """The lockout counts fails in a MODULE-level dict, so there is one per
    uvicorn worker and an attacker is spread across all of them by the accept
    queue. Its comment said "single-process hobby scale"; the live public API
    runs WEB_CONCURRENCY=2, so the stated 8 attempts were really 16.

    ratelimit.py already divides its limits by the worker count for exactly
    this reason. This asserts the two agree rather than asserting a number,
    so raising concurrency cannot silently weaken it again.
    """
    import api.auth as auth
    import ratelimit

    assert auth._LOGIN_FAIL_MAX == ratelimit._per_worker(auth.LOGIN_FAIL_MAX_TOTAL)
    # and never zero, which would lock every account out permanently
    assert auth._LOGIN_FAIL_MAX >= 1
