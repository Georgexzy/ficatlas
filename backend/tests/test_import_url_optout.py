"""Regression tests for metadata-only public listings and private copies."""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import httpx
from sqlalchemy import text
from fastapi import HTTPException
from models.user import User
from api import library as library_api
from models.story import Chapter, RatingEnum, SiteEnum, StatusEnum, Story
from api.stories import may_read_text

AO3_URL = "https://archiveofourown.org/works/123456"
OPTOUT_SUMMARY = "Please do not repost this work on any other website."


@pytest.fixture()
def admin_user(db):
    u = User(username="admin_user", password_hash="x", role="owner")
    db.add(u)
    db.commit()
    return u


def _stub_fetchers(monkeypatch, summary):
    """Route the import's network/parse steps to canned values."""
    async def fake_fetch_from_fichub(url):
        return {"epub_url": "https://example.com/book.epub", "urls": {"epub": "https://example.com/book.epub"}}

    async def fake_fetch_epub_bytes(epub_url):
        return b"PK\x03\x04fake-epub"

    def fake_parse_epub(data):
        return {
            "title": "My Story",
            "author": "Jane Doe",
            "summary": summary,
            "language": "English",
            "rating": "T",
            "status": "complete",
            "word_count": 1000,
            "chapter_count": 1,
            "chapters": [{"title": "Chapter 1", "content": "It began.", "number": 1, "word_count": 1000}],
            "fandoms": ["Harry Potter"],
            "tags": [],
            "updated_at": "2024-01-01T00:00:00Z",
        }

    monkeypatch.setattr(library_api, "fetch_from_fichub", fake_fetch_from_fichub)
    monkeypatch.setattr(library_api, "fetch_epub_bytes", fake_fetch_epub_bytes)
    monkeypatch.setattr(library_api, "parse_epub", fake_parse_epub)
    monkeypatch.setattr(library_api, "_fetch_shared_metadata", lambda url: {
        "url": url, "site_id": "123456", "title": "My Story",
        "author": "Jane Doe", "summary": summary, "chapter_count": 1,
        "status": "complete", "language": "English",
    })


def test_public_import_with_optout_raises_403(monkeypatch, db, admin_user):
    _stub_fetchers(monkeypatch, OPTOUT_SUMMARY)
    with pytest.raises(HTTPException) as e:
        asyncio.run(library_api.import_url(url=AO3_URL, private=False, db=db, viewer=admin_user))
    assert e.value.status_code == 403
    # Nothing was written to the shared index.
    from models.story import Story
    assert db.query(Story).filter(Story.url == AO3_URL).count() == 0


def test_private_import_with_optout_is_allowed(monkeypatch, db, admin_user):
    # A private import keeps a personal copy the author's opt-out (which is about
    # *publishing elsewhere*) does not cover, so it must NOT be refused.
    _stub_fetchers(monkeypatch, OPTOUT_SUMMARY)
    result = asyncio.run(library_api.import_url(url=AO3_URL, private=True, db=db, viewer=admin_user))
    assert "id" in result


def test_public_import_without_optout_proceeds(monkeypatch, db, admin_user):
    _stub_fetchers(monkeypatch, "A perfectly ordinary summary.")
    async def no_epub(_url):
        raise AssertionError("shared listing must not request an EPUB")
    monkeypatch.setattr(library_api, "fetch_from_fichub", no_epub)
    result = asyncio.run(library_api.import_url(url=AO3_URL, private=False, db=db, viewer=admin_user))
    assert "id" in result
    assert result["listing_only"] is True
    story = db.query(Story).filter(Story.url == AO3_URL).one()
    assert story.is_hosted is False
    assert story.private_only is False
    assert db.query(Chapter).filter(Chapter.story_id == story.id).count() == 0


def test_public_import_does_not_expose_private_copy(monkeypatch, db, admin_user):
    _stub_fetchers(monkeypatch, "A perfectly ordinary summary.")
    private = asyncio.run(library_api.import_url(
        url=AO3_URL, private=True, db=db, viewer=admin_user))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(library_api.import_url(
            url=AO3_URL, private=False, db=db, viewer=admin_user))
    assert exc.value.status_code == 409
    story = db.query(Story).filter(Story.id == private["id"]).one()
    assert story.private_only is True
    assert story.is_hosted is False
    assert db.query(Chapter).filter(Chapter.story_id == story.id).count() == 1


def test_private_copy_on_public_listing_stays_owner_only(monkeypatch, db, admin_user):
    _stub_fetchers(monkeypatch, "A perfectly ordinary summary.")
    listing = asyncio.run(library_api.import_url(
        url=AO3_URL, private=False, db=db, viewer=admin_user))
    _stub_fetchers(monkeypatch, "Private EPUB summary that must not replace the listing.")
    private = asyncio.run(library_api.import_url(
        url=AO3_URL, private=True, db=db, viewer=admin_user))
    story = db.query(Story).filter(Story.id == listing["id"]).one()
    assert private["id"] == listing["id"]
    assert story.is_hosted is False
    assert story.private_only is False  # The metadata listing remains searchable.
    assert story.summary == "A perfectly ordinary summary."
    assert db.query(Chapter).filter(Chapter.story_id == story.id).count() == 1
    assert may_read_text(db, story, admin_user) is True
    assert may_read_text(db, story, None) is False
    other = User(username="another_reader", password_hash="x")
    db.add(other)
    db.commit()
    assert may_read_text(db, story, other) is False


def test_private_import_reuses_indexed_url_variant_and_existing_chapters(monkeypatch, db, admin_user):
    story = Story(
        site=SiteEnum.ao3, site_id="123456", url=AO3_URL, title="Existing work",
        author="Author", rating=RatingEnum.teen, status=StatusEnum.in_progress,
        is_hosted=False, private_only=False,
    )
    db.add(story)
    db.flush()
    db.add(Chapter(story_id=story.id, number=1, title="One",
                   content="<p>Already imported.</p>", word_count=2))
    db.commit()

    async def should_not_fetch(_url):
        raise AssertionError("an equivalent archive URL should reuse its chapters")

    monkeypatch.setattr(library_api, "fetch_from_fichub", should_not_fetch)
    result = asyncio.run(library_api.import_url(
        url=AO3_URL + "?view_full_work=true", private=True,
        db=db, viewer=admin_user))

    assert result["id"] == str(story.id)
    assert result["already_hosted"] is True
    assert db.execute(
        text("SELECT count(*) FROM user_hosted WHERE user_id=:u AND story_id=:s"),
        {"u": str(admin_user.id), "s": str(story.id)},
    ).scalar() == 1


def test_ao3_transport_timeout_uses_direct_import_fallback(monkeypatch, db, admin_user):
    async def timed_out(_url):
        raise httpx.ReadTimeout("FicHub stalled")

    async def unexpected_epub(_url):
        raise AssertionError("EPUB fetch should not run after the metadata request times out")

    monkeypatch.setattr(library_api, "fetch_from_fichub", timed_out)
    monkeypatch.setattr(library_api, "fetch_epub_bytes", unexpected_epub)
    monkeypatch.setattr(library_api, "parse_epub", lambda _data: {})
    monkeypatch.setattr("ao3_import.fetch_ao3_full_work", lambda _url: {
        "title": "Direct copy", "author": "Author", "summary": "Summary",
        "language": "English", "word_count": 3,
        "chapters": [{"number": 1, "title": "One",
                      "content": "<p>One paragraph.</p>", "word_count": 3}],
    })

    result = asyncio.run(library_api.import_url(
        url=AO3_URL, private=True, db=db, viewer=admin_user))
    assert result["title"] == "Direct copy"
    assert result["chapters"] == 1
