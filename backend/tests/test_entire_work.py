"""The entire-work reader hands over the whole text at once — so it must not be
the endpoint that forgets the gate or the sanitiser.

`GET /api/stories/{id}/entire` is a second way out of the same bytes the chapter
reader serves, added so a multi-chapter work can be read on one continuous scroll
the way AO3's "Entire Work" works. Two things make it worth pinning: it is new,
and it returns far more text per request than anything else on the site. So:

  1. `permit_text_read` decides here exactly as it does for a chapter —
     delisted, withdrawn, login-required and private-import all answered
     identically. A gate written out at each call site is a gate that drifts,
     and on this site it already drifted once (see test_delisted_gate.py).
  2. The bytes are the SINGLE-CHAPTER endpoint's bytes, byte for byte, through
     `_chapter_full` — there is no second place an unsanitised field can leave
     the process.
  3. Paging covers every stored chapter exactly once, in order, at any budget. A
     gap is a chapter the reader asked for and never saw; a repeat is a chapter
     read twice inside one document with no way to tell them apart.
"""
import os
import sys
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.stories as stories
from api.stories import get_chapter, get_entire_work
from db.session import get_db
from models.user import User


def _work(db, bodies, *, delisted=False, text_withdrawn=False, is_hosted=True):
    """A hosted story with one chapter per body, numbered from 1."""
    nonce = uuid.uuid4().hex
    sid = db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, chapter_count, is_hosted)
        VALUES ('ao3', :sid, :url, 'A Work', 'An Author', :n, :hosted)
        RETURNING id
    """), {"sid": nonce[:16], "url": f"https://example.test/{nonce}",
           "n": len(bodies), "hosted": is_hosted}).scalar()
    for i, body in enumerate(bodies, start=1):
        db.execute(text("""
            INSERT INTO chapters (story_id, number, title, content, word_count)
            VALUES (:s, :n, :t, :c, 3)
        """), {"s": sid, "n": i, "t": f"Chapter {i}", "c": body})
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


def _whole(db, sid, **kw):
    """Every page of a work, followed until the server says it is done.

    Asserts the one property paging exists for while collecting: no chapter is
    skipped and none comes back twice. The loop guard means a server that never
    answers `next_after = null` fails this test rather than hanging it.
    """
    seen, after, pages = [], 0, 0
    while True:
        page = get_entire_work(after=after, story_id=sid, db=db, viewer=None, **kw)
        nums = [c.number for c in page.chapters]
        # Compared against the chapters read on PREVIOUS pages — `seen` is
        # updated after this check, or every page would overlap itself.
        overlap = set(nums) & set(seen)
        seen += nums
        pages += 1
        assert pages < 50, "paging never terminated"
        if page.next_after is None:
            assert not overlap, f"page repeated a chapter already read: {overlap}"
            return seen, pages
        # The next page must start where this one ended, and must not re-send
        # anything already read.
        assert page.next_after > after
        assert page.next_after == nums[-1]
        assert not overlap, f"page repeated a chapter already read: {overlap}"
        after = page.next_after


@pytest.fixture()
def client(db):
    from api.stories import router as stories_router
    app = FastAPI()
    app.include_router(stories_router, prefix="/api/stories")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class TestSameGateAsTheChapterReader:
    def test_a_hosted_work_reads(self, db):
        s = _work(db, ["<p>one</p>", "<p>two</p>"])
        page = get_entire_work(after=0, story_id=s, db=db, viewer=None)
        assert [c.number for c in page.chapters] == [1, 2]
        assert page.next_after is None

    def test_delisted_is_gone(self, db):
        """Delisting outranks everything: the author asked for the ENTRY gone."""
        s = _work(db, ["<p>one</p>"], delisted=True)
        with pytest.raises(HTTPException) as exc:
            get_entire_work(after=0, story_id=s, db=db, viewer=None)
        assert exc.value.status_code == 404

    def test_withdrawn_is_451(self, db):
        s = _work(db, ["<p>one</p>"], text_withdrawn=True)
        with pytest.raises(HTTPException) as exc:
            get_entire_work(after=0, story_id=s, db=db, viewer=None)
        assert exc.value.status_code == 451

    def test_delisted_outranks_text_withdrawn(self, db):
        """404, not a 451 that still names the work. Same as the chapter reader,
        because both raise out of the same function."""
        s = _work(db, ["<p>one</p>"], delisted=True, text_withdrawn=True)
        with pytest.raises(HTTPException) as exc:
            get_entire_work(after=0, story_id=s, db=db, viewer=None)
        assert exc.value.status_code == 404

    def test_a_private_import_is_not_someone_else_s_book(self, db):
        """A work that is not on the public shelf is404, not 403: whether a
        private import exists is itself its owner's business."""
        s = _work(db, ["<p>one</p>"], is_hosted=False)
        with pytest.raises(HTTPException) as exc:
            get_entire_work(after=0, story_id=s, db=db, viewer=_user(db))
        assert exc.value.status_code == 404

    def test_an_unknown_story_is_404(self, db):
        with pytest.raises(HTTPException) as exc:
            get_entire_work(after=0, story_id=str(uuid.uuid4()), db=db, viewer=None)
        assert exc.value.status_code == 404

    def test_a_listing_with_no_stored_text_says_so(self, db):
        """Reachable: the story page offers "entire work" from the OUTLINE, and
        an outline can outlive its chapters (a takedown that emptied it, an
        import that stored metadata only). It must explain rather than 500."""
        s = _work(db, [])
        with pytest.raises(HTTPException) as exc:
            get_entire_work(after=0, story_id=s, db=db, viewer=None)
        assert exc.value.status_code == 404

    def test_the_login_gate_applies_here_too(self, db, monkeypatch):
        """REQUIRE_LOGIN_TO_READ exists so the text is not simply published to
        the open internet. Serving the whole book anonymously through a NEW
        endpoint would undo exactly what it holds shut."""
        s = _work(db, ["<p>one</p>", "<p>two</p>"])
        monkeypatch.setattr(stories, "REQUIRE_LOGIN_TO_READ", True)
        with pytest.raises(HTTPException) as exc:
            get_entire_work(after=0, story_id=s, db=db, viewer=None)
        assert exc.value.status_code == 401
        assert get_entire_work(after=0, story_id=s, db=db,
                               viewer=_user(db)).chapters[0].number == 1

    def test_the_route_is_wired(self, client, db):
        """Direct calls cannot catch a route that was never mounted, or mounted
        at a path Next.js does not proxy."""
        s = _work(db, ["<p>one</p>"])
        r = client.get(f"/api/stories/{s}/entire")
        assert r.status_code == 200
        assert [c["number"] for c in r.json()["chapters"]] == [1]
        assert r.json()["next_after"] is None
        assert client.get(f"/api/stories/{uuid.uuid4()}/entire").status_code == 404


class TestSameBytesAsTheChapterReader:
    def test_it_serves_exactly_what_the_chapter_endpoint_serves(self, db):
        """The choke-point claim, tested rather than asserted. Both go through
        `_chapter_full`; if one of them ever stops, these strings diverge and
        this fails — which is the whole point of comparing them instead of
        asserting on sanitisation twice with two copies of the expectation."""
        s = _work(db, [
            '<p onclick="steal()">Hi</p><script>alert(1)</script>'
            '<a href="javascript:evil()">x</a>',
            "<p>fine</p>",
        ])
        page = get_entire_work(after=0, story_id=s, db=db, viewer=None)
        for i, got in enumerate(page.chapters, start=1):
            assert got.content == get_chapter(i, story_id=s, db=db, viewer=None).content
        assert "<script" not in page.chapters[0].content
        assert "onclick" not in page.chapters[0].content

    def test_notes_are_sanitised_alongside_the_body(self, db):
        """`start_note`/`end_note` are injected into the same document as the
        body, so they go through the same function — which the comparison above
        already proves; this pins the intent in words a refactor can trip over,
        and checks the notes are the same bytes the chapter reader sends."""
        s = _work(db, ["<p>one</p>"])
        db.execute(text("""
            UPDATE chapters SET start_note = :a, end_note = :b WHERE story_id = :s
        """), {"a": "<b>note<script>x</script></b>", "b": "<i>end</i>", "s": s})
        db.commit()
        got = get_entire_work(after=0, story_id=s, db=db, viewer=None).chapters[0]
        one = get_chapter(1, story_id=s, db=db, viewer=None)
        assert (got.start_note, got.end_note) == (one.start_note, one.end_note)
        assert "<script" not in (got.start_note or "")


class TestPaging:
    def test_pages_cover_every_chapter_exactly_once_in_order(self, db, monkeypatch):
        s = _work(db, [f"<p>chapter {i} body</p>" for i in range(1, 6)])
        monkeypatch.setattr(stories, "ENTIRE_MAX_CHAPTERS", 2)
        seen, pages = _whole(db, s)
        assert seen == [1, 2, 3, 4, 5]
        assert pages == 3, "five chapters, two per page, is three pages"

    def test_the_first_chapter_is_taken_whole_whatever_its_size(self, db, monkeypatch):
        """The byte budget exists to stop a RESPONSE being enormous, not to stop
        a single chapter being opened. Exempting the first chapter means a work
        whose first (or only) chapter is half a megabyte still reads — it just
        costs one big page. Otherwise a tiny budget would return an empty first
        page with `next_after = 0`, which the reader reads as "nothing here"."""
        s = _work(db, ["x" * 5000, "y" * 5000])
        monkeypatch.setattr(stories, "ENTIRE_MAX_BYTES", 10)
        page = get_entire_work(after=0, story_id=s, db=db, viewer=None)
        assert [c.number for c in page.chapters] == [1]
        assert page.chapters[0].content.strip() != ""
        assert page.next_after == 1

    def test_a_byte_budget_splits_only_at_chapter_boundaries(self, db, monkeypatch):
        """Never mid-chapter: the response is a list of complete chapters, so
        every element in it can be rendered, saved offline and read as written.
        A cut chapter would be truncated text the reader has no way to detect."""
        s = _work(db, ["a" * 60, "b" * 60, "c" * 60])
        monkeypatch.setattr(stories, "ENTIRE_MAX_BYTES", 100)
        seen, pages = _whole(db, s)
        assert seen == [1, 2, 3]
        # and every chapter arrives with all of its text, never a slice of it
        for c in get_entire_work(after=0, story_id=s, db=db, viewer=None).chapters:
            expected = "a" * 60 if c.number == 1 else "b" * 60 if c.number == 2 else "c" * 60
            assert c.content.strip() == expected

    def test_after_selects_strictly_higher_chapters(self, db):
        s = _work(db, [f"<p>{i}</p>" for i in range(1, 5)])
        page = get_entire_work(after=2, story_id=s, db=db, viewer=None)
        assert [c.number for c in page.chapters] == [3, 4]

    def test_after_an_unknown_number_comes_back_finished_not_failed(self, db):
        """The reader asks with a number the server itself gave it last time. If
        the stored chapters have moved on since (renumbered, pruned by a
        takedown), the honest answer is "there is no more" — a 404 would be read
        as a failed page and retried forever over a work already on screen."""
        s = _work(db, ["<p>1</p>", "<p>2</p>"])
        page = get_entire_work(after=999, story_id=s, db=db, viewer=None)
        assert page.chapters == []
        assert page.next_after is None
