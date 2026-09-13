"""`status=ongoing` filtered nothing at all, and said nothing about it.

The API coerced its `status` parameter through `StatusEnum.__members__`, whose
names are the storage spellings — `complete`, `in_progress`, `abandoned`,
`unknown`. Every reader-facing synonym the search bar documents and accepts
(`ongoing`, `wip`, `incomplete`, `completed`) failed that membership test, so
the list came out empty and the filter was DROPPED.

Silent, and in the direction that looks like it worked: a request asking for
unfinished works got every work, ordered plausibly. Measured on one AOT search
against the live index, `status=ongoing` returned 21 and `status=in_progress`
returned 5.

It reached readers through `/api/search/extract`, which reads the word out of a
post ("preferably ongoing") and hands it straight back — so the half of every
extracted request that was a status filter had never once been applied.

One vocabulary now, `query_parser.STATUS_WORDS`, which is the same table the
search bar has always used.
"""
import os
import sys
import uuid

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.search import router as search_router
from db.session import get_db
from query_parser import STATUS_WORDS


@pytest.fixture()
def client(db):
    """Through the HTTP layer, because that is where the coercion lives.

    `status` is a `Query(...)` parameter and the defaulting of every OTHER
    parameter is FastAPI's job — calling `search()` as a function hands it
    `Query` objects instead of values. The bug was in how a string off the wire
    becomes an enum, so the wire is where it has to be tested."""
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _story(db, *, status, title):
    nonce = uuid.uuid4().hex
    db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, status, tags)
        VALUES ('ao3', :sid, :url, :t, 'An Author', :st, ARRAY['Zebrafish Rodeo'])
    """), {"sid": nonce[:16], "url": f"https://example.test/{nonce}",
           "t": title, "st": status})
    db.commit()


def _totals(client, status):
    r = client.get("/api/search", params={"q": 'tag:"Zebrafish Rodeo"',
                                          "status": status})
    assert r.status_code == 200, r.text
    return r.json()["total"]


class TestStatusVocabulary:
    def test_every_spelling_of_unfinished_means_the_same_filter(self, db, client):
        """`ongoing`, `wip` and `incomplete` are what readers type, the search
        bar parses all three, and the API accepted none of them."""
        _story(db, status="in_progress", title="Still Going")
        _story(db, status="complete", title="All Done")
        for spelling in ("ongoing", "wip", "incomplete", "in_progress"):
            assert _totals(client, spelling) == 1, spelling

    def test_every_spelling_of_finished_means_the_same_filter(self, db, client):
        _story(db, status="in_progress", title="Still Going")
        _story(db, status="complete", title="All Done")
        for spelling in ("complete", "completed"):
            assert _totals(client, spelling) == 1, spelling

    def test_a_word_nobody_uses_still_filters_nothing(self, db, client):
        """The permissive half has to stay permissive. An unrecognised value is
        not an error — the filter simply does not apply — because a 422 on a
        stray word would break links people have already shared."""
        _story(db, status="in_progress", title="Still Going")
        _story(db, status="complete", title="All Done")
        assert _totals(client, "banana") == 2

    def test_the_bar_and_the_api_read_one_table(self):
        """The two vocabularies drifting apart is the bug itself. Assert they
        are the same object rather than the same contents, so a spelling added
        to the bar cannot be added without the API getting it."""
        import api.search as s
        assert s.STATUS_WORDS is STATUS_WORDS
        assert set(STATUS_WORDS) >= {"ongoing", "wip", "complete", "completed"}
