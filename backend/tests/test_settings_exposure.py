"""GET /api/settings must not publish the operator's key/value store.

It returned the WHOLE app_settings table, unauthenticated — 140 keys on the live
instance, of which 6 are the reader-facing defaults the Settings page wants. The
rest is operator state: crawl mode, rotation cursors, per-site circuit breakers,
`archive_page:*`/`listing_page:*` harvest watermarks, the admin panel's growth
samples.

Nothing in it was a credential. The fault is the DIRECTION of the default: this
table is a general store that server-side code writes to, so public-unless-someone-
thinks-about-it means the next key added is published by accident. These tests pin
the allowlist, and pin it from both ends — a reader sees only PUBLIC_KEYS, and an
admin still sees everything, because a trimmed response for an admin would quietly
break the site-settings form instead.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.settings import PUBLIC_KEYS, DEFAULTS, all_settings, put_setting  # noqa: E402
from models.user import User, ROLE_ADMIN, ROLE_OWNER, ROLE_READER  # noqa: E402


def _seed(db):
    """An operator key of the kind the worker writes without thinking about it."""
    put_setting(db, "crawl_rotate_cursor", "1234")
    db.add(User(username="somereader", password_hash="x", role=ROLE_READER))
    db.commit()


def test_an_anonymous_caller_sees_only_the_public_defaults(db):
    _seed(db)
    out = all_settings(db=db, user=None)
    assert set(out) == PUBLIC_KEYS
    assert "crawl_rotate_cursor" not in out


def test_a_signed_in_reader_sees_only_the_public_defaults(db):
    _seed(db)
    reader = db.query(User).filter(User.username == "somereader").one()
    out = all_settings(db=db, user=reader)
    assert set(out) == PUBLIC_KEYS


def test_an_admin_still_sees_everything(db):
    """The direction that breaks the site-settings form if it regresses."""
    _seed(db)
    admin = User(username="anadmin", password_hash="x", role=ROLE_ADMIN)
    db.add(admin); db.commit()
    out = all_settings(db=db, user=admin)
    assert out["crawl_rotate_cursor"] == "1234"
    # Every key the admin form posts has to come back, or it edits blanks over
    # real values.
    for k in ("tracked_fandom", "crawl_mode", "live_fetch", "enable_direct_crawl",
              "feed_min_words", "feed_complete_only", "poll_on_load",
              "crawl_rotate_count"):
        assert k in out, k


def test_an_unclaimed_instance_falls_open(db):
    """POST falls open until the first account exists so a fresh install can be
    configured; a GET that stayed shut would show that operator a form they can
    save but not read back."""
    put_setting(db, "crawl_rotate_cursor", "7")
    assert db.query(User).first() is None
    assert all_settings(db=db, user=None)["crawl_rotate_cursor"] == "7"


def test_the_public_set_is_a_subset_of_the_known_keys(db):
    """A public key that is not a real setting would be silently undefined."""
    assert PUBLIC_KEYS <= set(DEFAULTS)


def test_nothing_credential_shaped_is_public(db):
    import re
    for k in PUBLIC_KEYS:
        assert not re.search(r"token|secret|password|cursor|watermark", k, re.I), k
