"""Verification, and the one decision that decides whether a work is hosted.

These matter more than most tests in this repo, because the two ways of being
wrong are not equally bad. Refusing to host something we were allowed to host
annoys one author. Hosting something we were not allowed to host is the harm the
whole feature exists to prevent, and the person it happens to may never find out.
So the asymmetry is asserted directly rather than left implied.
"""
import pytest

import author_permission as ap
from author_permission import (
    normalise_author, profile_url, token_present, extract_evidence, new_token,
)


# ── Token matching ───────────────────────────────────────────────────────────

def test_token_must_appear_exactly():
    t = new_token()
    assert token_present(f"hello {t} world", t)
    # Near-misses are not matches. The token comes from `secrets`, so an exact
    # appearance cannot be coincidence — and anything looser widens what counts
    # as proof of identity.
    assert not token_present(t[:-1], t)
    assert not token_present(t.upper(), t)
    assert not token_present(t.replace("-", " "), t)


def test_empty_inputs_never_verify():
    assert not token_present("", "abc")
    assert not token_present("some page", "")
    assert not token_present(None, "abc")


def test_tokens_are_unique_and_prefixed():
    seen = {new_token() for _ in range(200)}
    assert len(seen) == 200
    assert all(t.startswith(ap.TOKEN_PREFIX) for t in seen)


def test_evidence_captures_context_and_strips_markup():
    t = new_token()
    page = f"<div class='bio'><p>I write things. {t} </p></div>"
    ev = extract_evidence(page, t)
    assert t in ev
    assert "I write things." in ev
    assert "<p>" not in ev and "<div" not in ev


def test_evidence_is_empty_when_absent():
    assert extract_evidence("nothing here", new_token()) == ""


# ── Identity plumbing ────────────────────────────────────────────────────────

def test_author_key_is_case_insensitive():
    assert normalise_author("  SomeWriter  ") == normalise_author("somewriter")


def test_profile_urls():
    assert profile_url("ao3", "somewriter").endswith("/users/somewriter/profile")
    # FF.net profiles are numeric; accept a bare id or a pasted profile link.
    assert profile_url("ffnet", "12345") == "https://www.fanfiction.net/u/12345/"
    assert profile_url("ffnet", "https://www.fanfiction.net/u/12345/Someone") \
        == "https://www.fanfiction.net/u/12345/"
    # A name alone cannot address an FF.net profile, so refuse rather than guess.
    assert profile_url("ffnet", "somewriter") is None
    assert profile_url("ao3", "") is None
    assert profile_url("nosuchsite", "x") is None


# ── The hosting decision ─────────────────────────────────────────────────────

class _FakeDB:
    """Stands in for the permissions table. decide_hosting only reads it."""
    def __init__(self, policy=None):
        self.policy = policy


@pytest.fixture(autouse=True)
def _stub_lookup(monkeypatch):
    monkeypatch.setattr(ap, "get_permission",
                        lambda db, site, author:
                        {"policy": db.policy} if getattr(db, "policy", None) else None)


REFUSAL = "do not repost this anywhere else"


def test_private_import_is_never_blocked():
    """The owner's own library is not republication, so no third party's
    consent is engaged — including when the author has said no."""
    for policy in (None, "deny", "metadata_only"):
        allowed, _ = ap.decide_hosting(
            _FakeDB(policy), site="ao3", author="w", summary=REFUSAL, private=True)
        assert allowed is True, policy


def test_no_record_falls_back_to_the_summary_heuristic():
    allowed, _ = ap.decide_hosting(
        _FakeDB(), site="ao3", author="w", summary=REFUSAL, private=False)
    assert allowed is False
    allowed, reason = ap.decide_hosting(
        _FakeDB(), site="ao3", author="w", summary="A nice story.", private=False)
    assert allowed is True and reason is None


def test_verified_yes_overrides_a_false_positive_optout():
    """The only way to correct the detector, exercisable only by the author."""
    allowed, _ = ap.decide_hosting(
        _FakeDB("host"), site="ao3", author="w", summary=REFUSAL, private=False)
    assert allowed is True


def test_verified_no_blocks_however_harmless_the_summary_looks():
    for policy in ("deny", "metadata_only"):
        allowed, reason = ap.decide_hosting(
            _FakeDB(policy), site="ao3", author="w",
            summary="A perfectly ordinary summary.", private=False)
        assert allowed is False, policy
        assert reason and "author" in reason.lower()


def test_missing_author_still_consults_the_heuristic():
    """Rows arrive with no author; that must not become an implicit yes."""
    allowed, _ = ap.decide_hosting(
        _FakeDB(), site="ao3", author="", summary=REFUSAL, private=False)
    assert allowed is False
