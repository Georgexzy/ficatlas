"""A delisted work is gone — from every endpoint that hands over its text.

Delisting is the strongest action the site takes: the author asked for the
entry removed, and the story page 404s rather than tombstone so the author's
name is not republished. But the gate used to live only in get_story. The
chapter reader and the EPUB exporter checked text_withdrawn_at and ownership
but never delisted_at, so the full text of a delisted work stayed one guessed
URL away — the author's withdrawal was a locked front door with the back
windows open.

These tests hold the same gate shut across all three endpoints, and pin the
admin escape hatch the reversal policy needs.
"""
import os
import sys
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.stories import get_story, get_chapter, export_epub
from models.user import User


def _hosted_story(db, *, delisted=False, text_withdrawn=False):
    """A hosted story with one chapter. Delisting/withdrawal applied on demand."""
    nonce = uuid.uuid4().hex
    sid = db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, chapter_count, is_hosted)
        VALUES ('ao3', :sid, :url, 'A Work', 'An Author', 1, true)
        RETURNING id
    """), {"sid": nonce[:16], "url": f"https://example.test/{nonce}"}).scalar()
    db.execute(text("""
        INSERT INTO chapters (story_id, number, title, content, word_count)
        VALUES (:s, 1, 'One', '<p>hello</p>', 3)
    """), {"s": sid})
    if delisted:
        db.execute(text("UPDATE stories SET delisted_at = now() WHERE id = :s"), {"s": sid})
    if text_withdrawn:
        db.execute(text("UPDATE stories SET text_withdrawn_at = now() WHERE id = :s"),
                   {"s": sid})
    db.commit()
    return str(sid)


def _user(db, role="reader"):
    uid = db.execute(text("""
        INSERT INTO users (username, password_hash, role)
        VALUES (:n, 'x', :r) RETURNING id
    """), {"n": f"u_{uuid.uuid4().hex[:10]}", "r": role}).scalar()
    db.commit()
    return db.get(User, uid)


def _chapter_404(fn, **kw):
    with pytest.raises(HTTPException) as exc:
        fn(**kw)
    assert exc.value.status_code == 404


class TestDelistedGate:
    def test_hosted_story_still_readable_when_not_delisted(self, db):
        """The new gate must not over-block: a normal hosted work reads fine."""
        s = _hosted_story(db)
        ch = get_chapter(1, story_id=s, db=db, viewer=None)
        assert ch.number == 1
        out = export_epub(story_id=s, db=db, viewer=None)
        assert out.media_type == "application/epub+zip"
        detail = get_story(story_id=s, db=db, viewer=None)
        assert detail.id == s

    def test_delisted_chapter_is_gone_for_anonymous(self, db):
        s = _hosted_story(db, delisted=True)
        _chapter_404(get_chapter, number=1, story_id=s, db=db, viewer=None)

    def test_delisted_export_is_gone_for_anonymous(self, db):
        s = _hosted_story(db, delisted=True)
        _chapter_404(export_epub, story_id=s, db=db, viewer=None)

    def test_delisted_story_page_is_gone_for_anonymous(self, db):
        s = _hosted_story(db, delisted=True)
        _chapter_404(get_story, story_id=s, db=db, viewer=None)

    def test_delisted_work_is_gone_for_a_reader_account(self, db):
        """Signing in does not reopen a delisted work — only admin does."""
        s = _hosted_story(db, delisted=True)
        _chapter_404(get_chapter, number=1, story_id=s, db=db, viewer=_user(db, "reader"))

    def test_admin_can_still_open_a_delisted_work(self, db):
        """The reversal the policy promises needs someone who can see it."""
        s = _hosted_story(db, delisted=True)
        admin = _user(db, "admin")
        assert get_story(story_id=s, db=db, viewer=admin).id == s
        assert get_chapter(1, story_id=s, db=db, viewer=admin).number == 1
        assert export_epub(story_id=s, db=db, viewer=admin).media_type == "application/epub+zip"

    def test_delisted_outranks_text_withdrawn(self, db):
        """An author who asked for the entry gone gets 404, not a 451 that
        still names their work."""
        s = _hosted_story(db, delisted=True, text_withdrawn=True)
        _chapter_404(get_chapter, number=1, story_id=s, db=db, viewer=None)
        _chapter_404(export_epub, story_id=s, db=db, viewer=None)
