"""Regression tests for the /import-url opt-out gate.

A PUBLIC import republishes someone's work under this site's name, so a summary
that explicitly refuses external reposting must be refused (403) — even when the
EPUB fetch succeeded. A PRIVATE import republishes nothing, so it is allowed
through. These call the endpoint function directly with the network fetchers
stubbed, so no real FicHub/AO3 traffic and no lifespan/init run against prod.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import HTTPException
from models.user import User
from api import library as library_api

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
    result = asyncio.run(library_api.import_url(url=AO3_URL, private=False, db=db, viewer=admin_user))
    assert "id" in result
