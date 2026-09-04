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
import hashlib
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


def test_a_visitor_id_cannot_be_reproduced_without_the_key(db):
    """The property that makes the table safe to keep.

    A hash of a 32-bit address space is trivially enumerable, so an UNKEYED
    digest would let anyone holding these rows ask "was this visitor at
    81.2.x.y?" and get an answer. Keying it means the question cannot be asked
    without the secret — which is why that secret lives in its own table and not
    in app_settings, where GET /api/settings hands it to anybody.

    (An earlier version of this test asserted that no octet of the address
    appeared anywhere in the hash. Single-digit octets appear in a 16-character
    hex string most of the time, so it was passing on luck and failed the first
    morning the daily rotation produced a hash containing a "4".)
    """
    ip, ua = "198.51.100.4", "Firefox"
    h = tracking.visitor_hash(ip, ua)
    assert len(h) == 16 and all(c in "0123456789abcdef" for c in h)
    assert ip not in h

    day = datetime.utcnow().strftime("%Y-%m-%d")
    unkeyed = hashlib.sha256(f"{day}|{ip}|{ua}".encode()).hexdigest()[:16]
    assert h != unkeyed, "the visitor id must be keyed, not a plain digest"


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
    # Assert the fields this test is about, not the whole row. Both visits hit
    # the same path, so "views == 1" IS the bot exclusion -- an equality check
    # against the entire dict also fails the day a purely additive field like
    # `label` is introduced, which is what happened and which says nothing about
    # whether crawlers are filtered.
    assert len(pages) == 1
    assert pages[0]["path"] == "/story/abc"
    assert pages[0]["views"] == 1
    assert pages[0]["visitors"] == 1


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


def test_search_rows_keep_the_time_of_day_not_just_the_date(db):
    """Rounding a search to its calendar day destroys the thing that separates
    one reader refining a phrase from several readers asking the same question.

    A real example from the live index: one query ran 474 times over seven days.
    As six dates that is unreadable; as timestamps it resolves into sessions of
    40-89 runs inside a couple of hours, which is a different fact about demand.

    /pages and /referrers deliberately still round to the day -- they are read
    as volume over a window. This asymmetry is the contract being pinned here.
    """
    v = tracking.visitor_hash("198.51.100.9", "Firefox")
    tracking.record("search", "/api/search", v, q="wolfstar", results=0)
    tracking.record("page", "/ship/remus-lupin-sirius-black", v)
    tracking.flush()

    out = traffic.searches(days=7, limit=10, db=db, _owner=None)
    for field in (out["top"][0]["first_seen"], out["top"][0]["last_seen"],
                  out["empty"][0]["last_seen"]):
        # "2026-09-02T13:13:41.816050", not "2026-09-02".
        assert "T" in field, f"search stamp lost its time: {field!r}"
        # Naive: the client marks these UTC before rendering them locally, and
        # an offset appearing here would mean it marks them twice.
        assert not field.endswith("Z") and "+" not in field, field

    # The day-grained reports must NOT have been dragged along with it.
    assert "T" not in traffic.pages(days=7, limit=10, db=db,
                                    _owner=None)["pages"][0]["last_seen"]


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


def test_a_search_with_no_total_records_nothing_rather_than_zero():
    """Through _note_total itself, which is where the confusion enters.

    The report test above calls tracking.record directly, so it could never have
    caught `int(getattr(payload, "total", None) or 0)` — which turned "no count
    available" into "found nothing" and would have put healthy queries in the
    empty-results list, sending the crawler after something already indexed.
    """
    from api.search import _note_total

    class _Req:
        def __init__(self):
            self.state = type("S", (), {})()

    absent = _Req()
    _note_total(absent, object())                       # payload has no .total
    assert getattr(absent.state, "search_total", None) is None

    none_total = _Req()
    _note_total(none_total, type("P", (), {"total": None})())
    assert getattr(none_total.state, "search_total", None) is None

    # A real zero still records as zero — that IS a search that found nothing.
    genuine = _Req()
    _note_total(genuine, type("P", (), {"total": 0})())
    assert genuine.state.search_total == 0


def test_a_deploy_does_not_lose_the_last_few_seconds(db):
    """Cancelling the writer is what flushes it, and cancelling is how shutdown
    arrives. This ran green while the events were in fact being thrown away on
    every promote, because main.py cancelled the task without awaiting it — the
    handler needs the loop to run once more. Pinned here at the flush_loop end;
    the await is what makes it reachable."""
    import asyncio
    import contextlib

    async def scenario():
        task = asyncio.create_task(tracking.flush_loop())
        await asyncio.sleep(0.05)               # let it reach its first sleep
        tracking.record("page", "/last-second",
                        tracking.visitor_hash("198.51.100.11", "Firefox"))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert db.execute(text("""
        SELECT count(*) FROM visit_events WHERE path = '/last-second'
    """)).scalar() == 1


def test_paging_is_not_counted_as_searching_again(db):
    """Every results page is a second /api/search request carrying the same
    `q`. Without the page in the path the log cannot tell "searched again" from
    "went to page 4", and it read one reader working through a long result set
    as a dozen searches — a number quoted as evidence elsewhere in this
    codebase.
    """
    for path in ("/api/search", "/api/search?page=2", "/api/search?page=3"):
        tracking.record("search", path, "v" * 16, q="drarry", results=5000)
    tracking.flush()

    rep = traffic.searches(days=7, limit=10, db=db, _owner=None)
    assert rep["totals"]["runs"] == 1
    assert rep["top"][0]["query"] == "drarry"
    assert rep["top"][0]["runs"] == 1


def test_a_visitor_that_never_loaded_a_page_is_counted_apart(db):
    """The doubt the user-agent check cannot answer. Pageviews come from the
    browser beacon, so a visitor that searched and never rendered a page was
    not a browser. Measured on the live log: 18 such visitors accounted for 759
    of 1,370 recorded searches and produced no pageviews between them — one was
    a developer test session. `is_bot` saw none of it: it matches on the
    user-agent string, and says so in its own comment.
    """
    tracking.record("search", "/api/search", "s" * 16, q="scripted", results=1)
    tracking.record("search", "/api/search", "h" * 16, q="human", results=1)
    tracking.record("pageview", "/story/abc", "h" * 16)
    tracking.flush()

    totals = traffic.searches(days=7, limit=10, db=db, _owner=None)["totals"]
    assert totals["runs"] == 2
    assert totals["search_only"] == 1
