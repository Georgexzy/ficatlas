"""Telling an age gate apart from an author locking their work.

AO3 answers both with a redirect, and the code used to treat them identically —
"alive", which is true of both and loses the only difference that matters:

    view_adult   an age confirmation anyone can click through, logged in or not.
                 The work is as public as any other.
    users/login  registered users only. The author took the work out of public
                 view, which ~966,000 of AO3's ~11.7M works have done.

Getting this backwards in the permissive direction would mark nearly every
explicit work as author-restricted, since adult works are exactly the ones most
likely to be gated. That is the case these are here to hold shut.
"""
import httpx
import pytest

import withdraw_deleted as wd


class _Resp:
    """Minimal stand-in; check_source only reads these three."""
    def __init__(self, status, location=None, text=""):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.text = text


@pytest.fixture
def fake_get(monkeypatch):
    def _install(resp):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)
    return _install


def test_age_gate_is_not_a_restriction(fake_get):
    """The regression guard. An adult work is public; anyone can click through."""
    for target in ("https://archiveofourown.org/works/123?view_adult=true",
                   "/works/123?view_adult=true"):
        fake_get(_Resp(302, target))
        assert wd.check_source("https://archiveofourown.org/works/123") == "alive"


def test_login_redirect_is_a_restriction(fake_get):
    for target in ("https://archiveofourown.org/users/login?return_to=%2Fworks%2F123",
                   "/users/login"):
        fake_get(_Resp(302, target))
        assert wd.check_source("https://archiveofourown.org/works/123") == "restricted"


def test_restricted_in_the_target_counts_too(fake_get):
    fake_get(_Resp(302, "/works/123/restricted"))
    assert wd.check_source("https://archiveofourown.org/works/123") == "restricted"


def test_deleted_is_still_deleted(fake_get):
    for code in (404, 410):
        fake_get(_Resp(code))
        assert wd.check_source("https://archiveofourown.org/works/123") == "gone"


def test_a_redirect_we_do_not_recognise_claims_nothing(fake_get):
    """`unknown` is the safe default: it must not read as either alive or gone."""
    fake_get(_Resp(302, "https://example.com/somewhere-else"))
    assert wd.check_source("https://archiveofourown.org/works/123") == "unknown"


def test_restriction_is_never_confused_with_deletion(fake_get):
    """A locked work must never be withdrawn — it is the mature fic most likely
    to be gated, and withdrawing it would be the worst possible false positive."""
    fake_get(_Resp(302, "/users/login"))
    assert wd.check_source("https://archiveofourown.org/works/123") != "gone"
