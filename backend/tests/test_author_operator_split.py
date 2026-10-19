"""`author:Lomonaaeren time travel` searched for an author NAMED that, and
returned nothing.

The parser has to read a bare multi-word operator value as running to the end of
the query — that is the only rule that makes `tag:slow burn` and `author:Some
Long Pen Name` work, and nothing syntactic separates those from a name followed
by search terms. Only the vocabulary can, so `_resolve_or_split` cuts the value
back and hands the trimmed words to the free-text query.

Author support was written for it — its own branch in `_resolve_or_split`, its
own exact probe in `_author_is_known`, and a comment saying the same failure
"survived because nobody checked a second column". And then the single call site
was never updated: it passed fandoms, characters, relationships and tags, and
not author. So the branch was dead code, and the author's half of every such
query stayed unsplit.

Measured on the live index before this was fixed:

    author:Lomonaaeren time travel      ->    0 results
    author:Lomonaaeren                  ->  970 results
    time travel (no operator)           -> thousands

The failure is silent and it is the worst kind: two halves that each work, ANDed
to nothing, with no error and no empty-state explanation.

The lookup is case-INSENSITIVE because the author filter is — it compares
`lower(Story.author)` — so `author:white_squirrel` finds a stored
`White_Squirrel` on its own. A case-sensitive vocabulary probe meant the split
simply did not fire for lowercase, which is how people type and how every
failing query in the logs was written.
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


@pytest.fixture()
def client(db):
    """Through the HTTP layer, because that is where the wiring is.

    The bug was not in `_resolve_or_split` — that function was correct and had
    its own tests. It was that the search endpoint never called it for `author`.
    Calling `search()` directly would not have caught that, and neither does a
    unit test of the helper: the omission was one tuple in a for-loop."""
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _work(db, *, author, title, tags=()):
    nonce = uuid.uuid4().hex
    db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, status, tags)
        VALUES ('ao3', :sid, :url, :t, :a, 'complete', :tags)
    """), {"sid": nonce[:16], "url": f"https://example.test/{nonce}",
           "t": title, "a": author, "tags": list(tags)})
    # `author_facets` is the vocabulary the split asks, and it is built by the
    # admin refresh rather than from the stories table — so a seeded author is
    # not in it unless it is seeded here too.
    db.execute(text("""
        INSERT INTO author_facets (value, count) VALUES (:a, 33)
        ON CONFLICT (value) DO NOTHING
    """), {"a": author})
    db.commit()


def _total(client, q):
    r = client.get("/api/search", params={"q": q})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["total"], [s["author"] for s in body["results"]]


@pytest.fixture()
def seeded(db):
    _work(db, author="Quillforge", title="A Study in Blue",
          tags=("Zebrafish Rodeo",))
    _work(db, author="Quillforge", title="The Long Winter",
          tags=("Zebrafish Rodeo",))
    _work(db, author="Otherhand", title="Completely Different",
          tags=("Zebrafish Rodeo",))
    return db


class TestAuthorValueIsSplit:
    def test_trailing_words_become_free_text(self, db, client, seeded):
        """The regression itself. Unsplit, the filter is an author named
        "quillforge study" and matches nobody — 0 results, silently."""
        total, authors = _total(client, "author:quillforge study")
        assert total > 0
        assert set(authors) == {"Quillforge"}

    def test_the_lookup_is_case_insensitive(self, db, client, seeded):
        """How people type. The filter matches lower(Story.author), so the
        vocabulary has to agree or lowercase never splits at all."""
        for spelling in ("quillforge", "Quillforge", "QUILLFORGE"):
            total, authors = _total(client, f"author:{spelling} study")
            assert total > 0, spelling
            assert set(authors) == {"Quillforge"}, spelling

    def test_a_real_multi_word_pen_name_is_never_cut(self, db, client):
        """The other half of the contract, and the case the strict vocabulary
        exists to protect."""
        _work(db, author="Long Pen Name", title="A Fine Book")
        _work(db, author="Long Pen Name", title="Another Fine Book")
        total, authors = _total(client, 'author:"Long Pen Name"')
        assert total == 2
        assert set(authors) == {"Long Pen Name"}

    def test_a_name_nobody_has_is_left_alone(self, db, client, seeded):
        """An unknown author must keep behaving as it did, not silently become
        a text search over someone else's works."""
        total, _ = _total(client, "author:nobodyatever wrote this")
        assert total == 0

    def test_a_single_word_value_is_never_split(self, db, client, seeded):
        total, authors = _total(client, "author:quillforge")
        assert total == 2
        assert set(authors) == {"Quillforge"}


class TestAStatedFilterOutranksTheTitleRescue:
    """The near-miss title arm admits works whose TITLE matches, and it used to
    apply only the structural gate — so a reader who also named an author was
    answered with works by everybody else.

    `?author=White_Squirrel&q=animagus` returned twenty-seven works across
    nineteen authors, because "animagus" is a common title. It hid well, too:
    `&q=angst` filtered correctly, since "angst" is a tag and not a title, so
    the bug only surfaced for queries whose free text happened to resemble a
    title — which is exactly the query the rescue exists for.

    A rescue may loosen the TEXT requirement. It may not override a filter the
    reader typed, and there was nothing to tell them one had been dropped: the
    results were plausible, ranked, and silently not theirs.
    """

    @pytest.fixture()
    def two_authors_same_title(self, db):
        _work(db, author="Quillforge", title="Arithmancer Study")
        _work(db, author="Quillforge", title="The Quiet War")
        _work(db, author="Otherhand", title="Arithmancer")
        return db

    def test_another_authors_work_never_answers_a_filtered_search(
            self, db, client, two_authors_same_title):
        total, authors = _total(client, "author:quillforge arithmancer")
        assert total >= 1
        assert set(authors) == {"Quillforge"}, (
            "the title rescue answered with another author's work")

    def test_the_same_is_true_of_the_query_parameter_form(self, db, client,
                                                          two_authors_same_title):
        """The structured parameter is what the search bar actually sends, so
        this is not only an inline-syntax bug."""
        r = client.get("/api/search",
                       params={"author": "Quillforge", "q": "arithmancer"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 1
        assert {s["author"] for s in body["results"]} == {"Quillforge"}

    def test_the_rescue_still_works_when_nothing_was_stated(self, db, client,
                                                            two_authors_same_title):
        """The arm has to keep earning its place. With no filter given, both
        works titled "Arithmancer" are the answer — it is only the stated
        filter that outranks it."""
        total, _ = _total(client, "arithmancer")
        assert total >= 2
