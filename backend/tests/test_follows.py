"""Following a work, and what counts as "new".

The value of this feature is entirely in the arithmetic being right. A follow
list that cries wolf gets ignored, and one that misses a chapter is worse than
not existing — the reader stops checking the archive themselves because they
believe they would have been told.

Two behaviours carry most of that risk and are pinned here:

  * following records the CURRENT state as seen, so a new follow does not arrive
    with the work's whole back catalogue marked unread;
  * a work can LOSE chapters — an author unpublishing, or a metadata correction
    like the digit-concatenation repair that took 188188 back to 188 — and the
    difference must clamp at zero rather than showing "-3 new chapters".
"""
import json
import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _story(db, chapters=3, updated="2026-01-01"):
    return db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, chapter_count, updated_at)
        VALUES ('ao3', :sid, :url, 'A Work', 'An Author', :c, :u)
        RETURNING id
    """), {"sid": f"t{chapters}{updated}", "url": f"https://example.test/{chapters}{updated}",
           "c": chapters, "u": updated}).scalar()


def _user(db, name="follower"):
    return db.execute(text(
        "INSERT INTO users (username, password_hash) VALUES (:n, 'x') RETURNING id"
    ), {"n": name}).scalar()


def _follow(db, user_id, story_id):
    row = db.execute(text("SELECT chapter_count, updated_at FROM stories WHERE id = :s"),
                     {"s": story_id}).first()
    db.execute(text("""
        INSERT INTO follows (user_id, story_id, seen_chapters, seen_updated)
        VALUES (:u, :s, :c, :t) ON CONFLICT DO NOTHING
    """), {"u": user_id, "s": story_id, "c": row[0], "t": row[1]})
    db.commit()


def _unread(db, user_id):
    return db.execute(text("""
        SELECT count(*) FROM follows f JOIN stories s ON s.id = f.story_id
         WHERE f.user_id = :u AND s.delisted_at IS NULL
           AND (s.chapter_count > f.seen_chapters
                OR (s.updated_at IS NOT NULL AND f.seen_updated IS NOT NULL
                    AND s.updated_at > f.seen_updated))
    """), {"u": user_id}).scalar()


def _session(db, user_id):
    token = f"tok_{uuid.uuid4().hex}"
    db.execute(text("""
        INSERT INTO user_sessions (token, user_id, created_at, expires_at, last_used, user_agent, remember)
        VALUES (:t, :u, now(), now() + interval '90 days', now(), 'test', true)
    """), {"t": token, "u": user_id})
    db.commit()
    return token


def _picks(db, user_id, value):
    """What signup() writes: a comma-joined slug string in a JSONB column, so
    the JSON encoding has to be explicit — the column will not take the raw
    text, which is exactly the shape a hand-written INSERT gets wrong."""
    db.execute(text("""
        INSERT INTO user_data (user_id, key, value)
        VALUES (:u, 'signup_fandoms', CAST(:v AS jsonb))
        ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value
    """), {"u": user_id, "v": json.dumps(value)})
    db.commit()


@pytest.fixture()
def client(db):
    from api.follows import router as follows_router
    from fastapi import FastAPI
    from db.session import get_db
    app = FastAPI()
    app.include_router(follows_router, prefix="/api/follows")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class TestNewness:
    def test_a_fresh_follow_has_nothing_unread(self, db):
        """Otherwise every follow arrives shouting about chapters the reader
        deliberately chose to start from."""
        u, s = _user(db), _story(db, chapters=12)
        _follow(db, u, s)
        assert _unread(db, u) == 0

    def test_a_new_chapter_is_unread(self, db):
        u, s = _user(db), _story(db, chapters=3)
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET chapter_count = 5 WHERE id = :s"), {"s": s})
        db.commit()
        assert _unread(db, u) == 1

    def test_a_later_timestamp_counts_even_with_no_new_chapter(self, db):
        """An edited or expanded chapter moves the date without changing the
        count, and that is still the work changing."""
        u, s = _user(db, "u2"), _story(db, chapters=3, updated="2026-01-01")
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET updated_at = '2026-06-01' WHERE id = :s"), {"s": s})
        db.commit()
        assert _unread(db, u) == 1

    def test_marking_seen_clears_it(self, db):
        u, s = _user(db, "u3"), _story(db, chapters=3)
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET chapter_count = 9 WHERE id = :s"), {"s": s})
        db.commit()
        assert _unread(db, u) == 1
        db.execute(text("""
            UPDATE follows f SET seen_chapters = s.chapter_count, seen_updated = s.updated_at
              FROM stories s WHERE s.id = f.story_id AND f.user_id = :u
        """), {"u": u})
        db.commit()
        assert _unread(db, u) == 0

    def test_losing_chapters_is_not_negative_news(self, db):
        """188188 -> 188 was a real correction in this index. The reader should
        see nothing, not "-188,000 new chapters"."""
        u, s = _user(db, "u4"), _story(db, chapters=200)
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET chapter_count = 20 WHERE id = :s"), {"s": s})
        db.commit()
        assert _unread(db, u) == 0
        seen, now = db.execute(text("""
            SELECT f.seen_chapters, s.chapter_count FROM follows f
              JOIN stories s ON s.id = f.story_id WHERE f.user_id = :u
        """), {"u": u}).first()
        assert max(0, now - seen) == 0


class TestScope:
    def test_a_delisted_work_disappears_from_the_list(self, db):
        """An author asked to be removed. Their work should not keep surfacing
        in someone's follow list, and there is nothing left to link to."""
        u, s = _user(db, "u5"), _story(db, chapters=3)
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET chapter_count = 8, delisted_at = now() "
                        "WHERE id = :s"), {"s": s})
        db.commit()
        assert _unread(db, u) == 0

    def test_follows_are_per_reader(self, db):
        a, b = _user(db, "reader_a"), _user(db, "reader_b")
        s = _story(db, chapters=3)
        _follow(db, a, s)
        db.execute(text("UPDATE stories SET chapter_count = 6 WHERE id = :s"), {"s": s})
        db.commit()
        assert _unread(db, a) == 1
        assert _unread(db, b) == 0

    def test_following_twice_is_idempotent(self, db):
        u, s = _user(db, "u6"), _story(db, chapters=3)
        _follow(db, u, s)
        _follow(db, u, s)
        n = db.execute(text("SELECT count(*) FROM follows WHERE user_id = :u"),
                       {"u": u}).scalar()
        assert n == 1

    def test_deleting_a_story_removes_the_follow(self, db):
        """ON DELETE CASCADE — a follow pointing at nothing would break the join
        that powers the whole list."""
        u, s = _user(db, "u7"), _story(db, chapters=3)
        _follow(db, u, s)
        db.execute(text("DELETE FROM stories WHERE id = :s"), {"s": s})
        db.commit()
        assert db.execute(text("SELECT count(*) FROM follows WHERE user_id = :u"),
                          {"u": u}).scalar() == 0


class TestWelcomeBack:
    def test_returns_follow_updates(self, db, client):
        u = _user(db, "wb_user")
        s = _story(db, chapters=3)
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET chapter_count = 5 WHERE id = :s"), {"s": s})
        db.commit()
        db.execute(text("UPDATE follows SET seen_chapters = 3 WHERE user_id = :u"), {"u": u})
        db.commit()
        tok = _session(db, u)
        r = client.get("/api/follows/welcome", cookies={"sat": tok})
        assert r.status_code == 200
        data = r.json()
        assert data["follow_unread"] == 1
        assert len(data["follow_works"]) == 1
        assert data["follow_works"][0]["id"] == str(s)

    def test_returns_resume_progress(self, db, client):
        u = _user(db, "wb_resume")
        s = _story(db, chapters=3)
        db.execute(text("""
            INSERT INTO user_data (user_id, key, value)
            VALUES (:u, 'progress', :v)
        """), {"u": u, "v": '{"' + str(s) + '": {"chapter": 2, "at": "2026-01-01T00:00:00"}}'})
        db.commit()
        tok = _session(db, u)
        r = client.get("/api/follows/welcome", cookies={"sat": tok})
        assert r.status_code == 200
        data = r.json()
        assert data["resume"] == {"story_id": str(s), "chapter": 2, "at": "2026-01-01T00:00:00"}

    def test_carries_status_and_a_follow_total(self, db, client):
        """The two facts the front door's follow nudge cannot work out itself.

        Whether the work is still being written — following a finished one is a
        promise of updates that will never arrive — and how many works the
        reader follows AT ALL. Zero unread is also what a reader following ten
        finished works looks like, so it cannot stand in for that number.
        """
        u = _user(db, "nudge")
        s = _story(db, chapters=3)
        db.execute(text("UPDATE stories SET status = 'in_progress' WHERE id = :s"),
                   {"s": s})
        db.execute(text("""
            INSERT INTO user_data (user_id, key, value)
            VALUES (:u, 'progress', :v)
        """), {"u": u, "v": json.dumps({str(s): {"chapter": 1, "at": "2026-01-01T00:00:00"}})})
        db.commit()
        tok = _session(db, u)

        data = client.get("/api/follows/welcome", cookies={"sat": tok}).json()
        assert data["follows"] == 0
        assert [c["status"] for c in data["continue_reading"]] == ["in_progress"]

        _follow(db, u, s)
        data = client.get("/api/follows/welcome", cookies={"sat": tok}).json()
        assert data["follows"] == 1
        # Following records the current state as seen, so a fresh follow has
        # nothing unread. The two counts answer different questions.
        assert data["follow_unread"] == 0

    def test_empty_for_signed_out(self, db, client):
        r = client.get("/api/follows/welcome")
        assert r.status_code == 200
        data = r.json()
        assert data["follow_unread"] == 0
        assert data["follow_works"] == []
        assert data["resume"] is None

    def test_empty_when_not_returning(self, db, client):
        """A user who visited the landing page recently should not see the banner
        again — it fires on RETURN, not on every home page load."""
        u = _user(db, "wb_recent")
        s = _story(db, chapters=3)
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET chapter_count = 5 WHERE id = :s"), {"s": s})
        db.commit()
        db.execute(text("UPDATE follows SET seen_chapters = 3 WHERE user_id = :u"), {"u": u})
        db.commit()
        tok = _session(db, u)
        # First call — user is "returning" (no last_home_visit yet)
        r1 = client.get("/api/follows/welcome", cookies={"sat": tok})
        assert r1.json()["follow_unread"] == 1
        # Second call within threshold — user is NOT returning, returns empty
        r2 = client.get("/api/follows/welcome", cookies={"sat": tok})
        data = r2.json()
        assert data["follow_unread"] == 0
        assert data["follow_works"] == []
        assert data["resume"] is None

    def test_shown_again_after_threshold(self, db, client, monkeypatch):
        """After the threshold elapses, the banner fires again."""
        from api import follows
        monkeypatch.setattr(follows, "_RETURNING_THRESHOLD_MIN", 0)
        u = _user(db, "wb_threshold")
        s = _story(db, chapters=3)
        _follow(db, u, s)
        db.execute(text("UPDATE stories SET chapter_count = 5 WHERE id = :s"), {"s": s})
        db.commit()
        db.execute(text("UPDATE follows SET seen_chapters = 3 WHERE user_id = :u"), {"u": u})
        db.commit()
        tok = _session(db, u)
        # First call — returning, banner fires
        r1 = client.get("/api/follows/welcome", cookies={"sat": tok})
        assert r1.json()["follow_unread"] == 1
        # Second call — with threshold 0, even 1ms later counts as returning again
        r2 = client.get("/api/follows/welcome", cookies={"sat": tok})
        assert r2.json()["follow_unread"] == 1


class TestYourFandoms:
    """The signup picker's promise, on the page that is supposed to keep it.

    `signup_fandoms` was written by signup() and read by nothing. These pin the
    two halves the signup copy makes: the picks come back as hub pages (a name
    and a link, not a raw slug), and they come back on EVERY landing page load
    rather than only when the welcome banner fires — a standing recommendation
    is not a "welcome back" notice, and gating it on the return threshold would
    make the strip appear and disappear between two visits seconds apart.
    """

    def _seed(self, db):
        for slug, name, n in [("harry-potter", "Harry Potter", 120000),
                              ("star-wars", "Star Wars", 80000),
                              ("pokemon", "Pokémon", 60000)]:
            db.execute(text(
                "INSERT INTO fandom_hubs (slug, name, variants, work_count) "
                "VALUES (:s, :n, ARRAY[:n], :c)"
            ), {"s": slug, "n": name, "c": n})
        db.commit()

    def test_resolved_to_hubs_in_the_order_the_reader_picked(self, db, client):
        self._seed(db)
        u = _user(db, "picks")
        _picks(db, u, "harry-potter,star-wars,pokemon")
        r = client.get("/api/follows/welcome", cookies={"sat": _session(db, u)})
        assert r.status_code == 200
        assert [(f["slug"], f["name"]) for f in r.json()["your_fandoms"]] == [
            ("harry-potter", "Harry Potter"),
            ("star-wars", "Star Wars"),
            ("pokemon", "Pokémon"),
        ]

    def test_an_unknown_slug_is_dropped_not_rendered_raw(self, db, client):
        """A chip that 404s is worse than no chip: the reader cannot tell a
        broken site from a fandom that stopped existing. The picker only ever
        offered real hubs, so anything else is a stale or hand-written value."""
        self._seed(db)
        u = _user(db, "stale")
        _picks(db, u, "gone-forever,harry-potter,  ,harry-potter")
        r = client.get("/api/follows/welcome", cookies={"sat": _session(db, u)})
        f = r.json()["your_fandoms"]
        # Dropped, and the duplicate/blank entries collapsed with it.
        assert [x["slug"] for x in f] == ["harry-potter"]

    def test_returned_even_when_the_banner_does_not_fire(self, db, client):
        self._seed(db)
        u = _user(db, "just_here")
        _picks(db, u, "pokemon")
        tok = _session(db, u)
        assert len(client.get("/api/follows/welcome",
                              cookies={"sat": tok}).json()["your_fandoms"]) == 1
        # Second load inside the return threshold: the banner is suppressed and
        # the fandoms must not be.
        data = client.get("/api/follows/welcome", cookies={"sat": tok}).json()
        assert data["follow_unread"] == 0
        assert [f["slug"] for f in data["your_fandoms"]] == ["pokemon"]

    def test_no_pick_and_no_account_are_both_empty(self, db, client):
        self._seed(db)
        u = _user(db, "nopick")
        tok = _session(db, u)
        assert client.get("/api/follows/welcome",
                          cookies={"sat": tok}).json()["your_fandoms"] == []
        assert client.get("/api/follows/welcome").json()["your_fandoms"] == []
