"""The signup fandom picker's promise, leg by leg.

The signup page offers a picker and says "We'll prioritize these in your
Following and recommendations. Change anytime in Settings." signup() wrote the
choice to `user_data.signup_fandoms` — and for as long as that was all that
happened, the sentence was false twice over: nothing read the key (no endpoint
returned it), and it was not in ALLOWED_KEYS, so Settings could not have
offered the control the sentence points at.

The three legs that make it true, one each here:

  * the key is readable and writable through the userdata API;
  * a REMOVAL sticks — this list is edited, not accumulated, so the merge is
    replace rather than the union bookmarks get;
  * a merge that does not mention the key leaves it alone.

What the front door does with it is in test_follows.TestYourFandoms; the
write side of signup itself is in test_signup_email.
"""
import os
import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.userdata import _merge_value, router as userdata_router  # noqa: E402
from db.session import get_db  # noqa: E402


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(userdata_router, prefix="/api/userdata")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _user(db, name):
    return db.execute(text(
        "INSERT INTO users (username, password_hash) VALUES (:n, 'x') RETURNING id"
    ), {"n": name}).scalar()


def _session(db, user_id):
    token = f"tok_{uuid.uuid4().hex}"
    db.execute(text("""
        INSERT INTO user_sessions (token, user_id, created_at, expires_at, last_used, user_agent, remember)
        VALUES (:t, :u, now(), now() + interval '90 days', now(), 'test', true)
    """), {"t": token, "u": user_id})
    db.commit()
    return token


def test_the_pick_can_be_read_and_changed_from_settings(db, client):
    """The half of the promise that says "Change anytime in Settings"."""
    u = _user(db, "setter")
    tok = _session(db, u)
    cookies = {"sat": tok}

    assert client.get("/api/userdata/signup_fandoms",
                      cookies=cookies).json()["value"] is None

    put = client.put("/api/userdata/signup_fandoms", cookies=cookies,
                     json="harry-potter,star-wars")
    assert put.status_code == 200
    assert client.get("/api/userdata/signup_fandoms",
                      cookies=cookies).json()["value"] == "harry-potter,star-wars"

    # Replace, so taking one off in Settings takes it off for good.
    client.put("/api/userdata/signup_fandoms", cookies=cookies, json="harry-potter")
    assert client.get("/api/userdata/signup_fandoms",
                      cookies=cookies).json()["value"] == "harry-potter"


def test_taking_a_fandom_off_survives_the_next_sync(db):
    """The union merge is right for a shelf and wrong for a list you edit.

    bookmarks/offline/saved union because a device that has never seen a work
    must not erase it. Here the opposite: the device holding the picker is the
    one that decided, and a union would bring a removed fandom straight back
    on the next sync from a device that never opened Settings.
    """
    server = "harry-potter,star-wars"
    client_value = "harry-potter"
    assert _merge_value("signup_fandoms", client_value, server) == "harry-potter"
    # And nothing is lost when the device with the fuller list is the one asking.
    assert _merge_value("signup_fandoms", server, client_value) == server


def test_a_merge_that_never_mentions_the_key_leaves_it_alone(db, client):
    """Every other device syncs a fixed set of keys; none of them carry this
    one, and a sync must not be able to empty it by omission."""
    u = _user(db, "omitter")
    tok = _session(db, u)
    client.put("/api/userdata/signup_fandoms", cookies={"sat": tok},
               json="pokemon")

    r = client.post("/api/userdata/merge", cookies={"sat": tok},
                    json={"recents": ["drarry"]})
    assert r.status_code == 200
    assert client.get("/api/userdata/signup_fandoms",
                      cookies={"sat": tok}).json()["value"] == "pokemon"


def test_the_guard_is_still_a_guard(db, client):
    """Adding one key to ALLOWED_KEYS must not open the door to any other.
    The list is the only thing standing between a reader and writing arbitrary
    rows into somebody else's account shape, so an unknown key is still 400."""
    u = _user(db, "guarded")
    tok = _session(db, u)
    assert client.put("/api/userdata/mute_list", cookies={"sat": tok},
                      json=["x"]).status_code == 400
    # The near-miss that matters: `fandoms` is what the mutes section on the
    # "Never show me" tab might be imagined to write, and `mutes.fandoms` is
    # where that list actually lives. The recommend-me key is `signup_fandoms`
    # and nothing else — an invented one is still 400.
    assert client.put("/api/userdata/fandoms", cookies={"sat": tok},
                      json=["harry-potter"]).status_code == 400
