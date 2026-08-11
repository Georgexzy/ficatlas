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
import os
import sys

import pytest
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
