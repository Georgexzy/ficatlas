"""Anonymous traffic: that it records something useful, and nothing personal.

The privacy properties here are not decoration — they are the reason the feature
is allowed to exist on a site whose whole content is what strangers like to
read. So they are tested as behaviour, not left as a claim in a comment:

  * no address and no user agent reach the database, in any column;
  * a visitor id cannot be linked from one day to the next;
  * a caller cannot say who it is, only what it looked at;
  * every report is owner-only, and that is checked from the route table rather
    than from each handler, so a report added later cannot quietly skip it.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import Depends, HTTPException
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracking
from api import traffic
from api.auth import require_owner
from models.user import ROLE_ADMIN, ROLE_OWNER, User


class _FakeRequest:
    """Just the parts client_ip() and the beacon actually read."""

    def __init__(self, ip="203.0.113.9", ua="Mozilla/5.0 (Macintosh)"):
        self.headers = {"user-agent": ua}
        self.client = type("C", (), {"host": ip})()


def _user(db, role="reader") -> User:
    uid = db.execute(text("""
        INSERT INTO users (username, password_hash, role)
        VALUES (:n, 'x', :r) RETURNING id
    """), {"n": f"u_{uuid.uuid4().hex[:10]}", "r": role}).scalar()
    db.commit()
    return db.get(User, uid)


@pytest.fixture(autouse=True)
def _drain():
    """The event buffer is module-level, so a leftover from another test would
    show up in this one's numbers."""
    tracking.flush()
    yield
    tracking.flush()


# ── what a visitor id is, and is not ────────────────────────────────────────

def test_the_same_visitor_is_stable_within_a_day(db):
    a = tracking.visitor_hash("198.51.100.4", "Firefox")
    b = tracking.visitor_hash("198.51.100.4", "Firefox")
    assert a == b and len(a) == 16


def test_a_visitor_cannot_be_followed_across_days(db):
    today = datetime(2026, 3, 1, 12, 0)
    tomorrow = today + timedelta(days=1)
    assert (tracking.visitor_hash("198.51.100.4", "Firefox", today)
            != tracking.visitor_hash("198.51.100.4", "Firefox", tomorrow))


def test_different_people_are_different_visitors(db):
    assert (tracking.visitor_hash("198.51.100.4", "Firefox")
            != tracking.visitor_hash("198.51.100.5", "Firefox"))
    assert (tracking.visitor_hash("198.51.100.4", "Firefox")
            != tracking.visitor_hash("198.51.100.4", "Chrome"))


def test_the_address_is_not_recoverable_from_the_hash(db):
    """Not a proof — a hash of a 32-bit space is guessable given the key. It is
    the reason the key lives in its own table and not in app_settings, which
    GET /api/settings hands to anybody."""
    ip = "198.51.100.4"
    h = tracking.visitor_hash(ip, "Firefox")
    assert ip not in h
    assert all(part not in h for part in ip.split("."))


# ── the beacon ──────────────────────────────────────────────────────────────

def test_a_pageview_is_recorded_with_no_trace_of_who(db):
    traffic.hit(_FakeRequest(ip="203.0.113.9", ua="Mozilla/5.0 (Macintosh)"),
                path="/story/abc", ref="https://reddit.com/r/HPfanfiction/comments/x")
    tracking.flush()

    row = db.execute(text("""
        SELECT kind, path, ref_host, visitor, bot FROM visit_events
    """)).first()
    assert row is not None
    assert (row[0], row[1]) == ("page", "/story/abc")
    # The referrer HOST only. The path someone arrived from can name a person.
    assert row[2] == "reddit.com"
    assert row[4] is False

    # Nothing in the row is the address or the user agent, in any column.
    everything = " ".join(str(c) for c in db.execute(
        text("SELECT * FROM visit_events")).first())
    assert "203.0.113.9" not in everything
    assert "Macintosh" not in everything


def test_a_caller_cannot_claim_to_be_somebody_else(db):
    """The visitor id is derived from the connection, never accepted from the
    caller — otherwise one script could invent a thousand readers."""
    traffic.hit(_FakeRequest(ip="203.0.113.1"), path="/a", ref="")
    traffic.hit(_FakeRequest(ip="203.0.113.1"), path="/b", ref="")
    tracking.flush()
    assert db.execute(text("SELECT count(DISTINCT visitor) FROM visit_events")).scalar() == 1


def test_an_absolute_url_is_not_recorded_as_a_page(db):
    """Otherwise the most-viewed-pages report becomes a stranger's billboard."""
    for bad in ("https://example.com/spam", "//example.com/spam"):
        traffic.hit(_FakeRequest(), path=bad, ref="")
    tracking.flush()
    assert db.execute(text("SELECT count(*) FROM visit_events")).scalar() == 0


def test_a_crawler_is_flagged_and_left_out_of_the_reports(db):
    traffic.hit(_FakeRequest(ua="Googlebot/2.1 (+http://www.google.com/bot.html)"),
                path="/story/abc", ref="")
    traffic.hit(_FakeRequest(ua="Mozilla/5.0 (iPhone)"), path="/story/abc", ref="")
    tracking.flush()

    assert db.execute(text("SELECT count(*) FROM visit_events WHERE bot")).scalar() == 1
    pages = traffic.pages(days=7, limit=10, db=db, _owner=None)["pages"]
    assert pages == [{"path": "/story/abc", "views": 1, "visitors": 1}]


def test_a_beacon_never_raises_at_the_caller(db, monkeypatch):
    """A page must not be able to fail over its own analytics."""
    monkeypatch.setattr(tracking, "record",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db is down")))
    r = traffic.hit(_FakeRequest(), path="/still/fine", ref="")
    assert r.status_code == 204


# ── the reports ─────────────────────────────────────────────────────────────

def test_searches_report_names_the_gaps(db):
    v = tracking.visitor_hash("198.51.100.7", "Firefox")
    tracking.record("search", "/api/search", v, q="drarry coffee shop", results=0)
    tracking.record("search", "/api/search", v, q="drarry coffee shop", results=0)
    tracking.record("search", "/api/search", v, q="time travel", results=812)
    tracking.flush()

    out = traffic.searches(days=7, limit=10, db=db, _owner=None)
    assert out["top"][0]["query"] == "drarry coffee shop"
    assert out["top"][0]["runs"] == 2
    # The report that answers "what should be crawled next".
    assert [e["query"] for e in out["empty"]] == ["drarry coffee shop"]


def test_visitors_are_never_summed_across_days(db):
    """A visitor id is per-day by design, so adding the daily counts would
    report a single regular as thirty people. The summary gives the busiest day
    instead, and says so."""
    v = tracking.visitor_hash("198.51.100.8", "Firefox")
    tracking.record("page", "/a", v)
    tracking.record("page", "/b", v)
    tracking.flush()

    out = traffic.summary(days=7, include_bots=False, db=db, _owner=None)
    assert out["totals"]["views"] == 2
    assert out["totals"]["busiest_day_visitors"] == 1
    assert "visitors" not in out["totals"]


def test_a_search_with_no_recorded_count_is_not_reported_as_finding_nothing(db):
    """NULL results means no exit recorded a count. Reporting that as 0 would
    put a healthy query in the "found nothing" list and send the crawler after
    something that is already indexed."""
    tracking.record("search", "/api/search",
                    tracking.visitor_hash("198.51.100.9", "Firefox"),
                    q="already indexed", results=None)
    tracking.flush()
    out = traffic.searches(days=7, limit=10, db=db, _owner=None)
    assert out["top"][0]["results"] is None
    assert out["empty"] == []


# ── who may read any of it ──────────────────────────────────────────────────

def _report_routes():
    return [r for r in traffic.router.routes if getattr(r, "path", "") != "/hit"]


def test_every_report_is_owner_gated():
    """Checked from the route table, not handler by handler: a report added
    later with an `admin` gate — or none — fails here rather than quietly
    shipping other people's browsing to whoever can run an import."""
    import inspect

    assert _report_routes(), "no reports found — has the router moved?"
    for route in _report_routes():
        gates = [
            p.default.dependency
            for p in inspect.signature(route.endpoint).parameters.values()
            if isinstance(p.default, type(Depends(require_owner)))
        ]
        assert require_owner in gates, f"{route.path} is not owner-only"


def test_the_beacon_stays_public():
    """The write side has to work for a reader who is not signed in at all —
    that is most of them."""
    import inspect

    hit_route = next(r for r in traffic.router.routes if getattr(r, "path", "") == "/hit")
    gates = [
        p.default.dependency
        for p in inspect.signature(hit_route.endpoint).parameters.values()
        if isinstance(p.default, type(Depends(require_owner)))
    ]
    assert gates == []


def test_an_admin_is_not_enough(db):
    """Deliberately stricter than the rest of /admin. Running the library and
    reading over the audience's shoulder are different permissions."""
    with pytest.raises(HTTPException) as exc:
        require_owner(user=_user(db, role=ROLE_ADMIN), db=db)
    assert exc.value.status_code == 403


def test_a_stranger_is_told_to_sign_in(db):
    _user(db, role=ROLE_OWNER)          # the instance is claimed
    with pytest.raises(HTTPException) as exc:
        require_owner(user=None, db=db)
    assert exc.value.status_code == 401


def test_the_owner_gets_in(db):
    owner = _user(db, role=ROLE_OWNER)
    assert require_owner(user=owner, db=db) is owner
