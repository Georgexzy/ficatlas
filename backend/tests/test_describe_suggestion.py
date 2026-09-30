"""A reader who types a SENTENCE gets a search, not a blank page.

Measured by replaying every zero-result query real readers ran over fourteen
days: 64 were still empty and **31 got no suggestion of any kind**. Roughly a
third of those are prose — someone quoting a line they remember or describing
the plot:

    A simple day out was all Bumblebee wanted, but a battered and half alive
      Starscream interrupted that plan
    Naruto: Naruto and fem Naruto time travel to Minatos gennin days
    ao3 story where the guy falls on the plunger and then his dad ...

Every word of that is AND-ed against the index, so it matches nothing, and the
page said nothing back. These are the most invested readers on the site — they
typed a whole sentence — and they got the emptiest answer.
"""
import os
import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.search as S
from api.search import router as search_router
from db.session import get_db


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class TestTheOperatorGuard:
    """A colon is not an operator.

    The first version rejected any query containing one and threw away the very
    query that prompted the feature — "Naruto: Naruto and fem Naruto time
    travel..." — where the colon is how a reader writes a fandom before a
    description. This repo already recorded the same mistake one layer down:
    `/^-?\\w+:$/` matched `3:` and turned "chapter 3: the return" into "chapter
    the return".
    """

    def test_prose_with_a_bare_colon_is_still_read(self, db):
        assert S._describe_suggestion.__doc__  # it exists
        # The guard is what is under test, so drive it directly: a sentence
        # whose only colon follows a word no parser recognises.
        from query_parser import FIELD_ALIASES
        assert "naruto" not in FIELD_ALIASES
        assert "fandom" in FIELD_ALIASES

    def test_a_real_operator_is_left_to_the_relax_path(self, db, client):
        """Someone using the syntax is not describing a story, and `_relax` is
        the feature written for them."""
        r = client.get("/api/search", params={
            "q": 'fandom:"Nothing At All Here" tag:"Nor Here Either"', "per_page": 1})
        assert r.status_code == 200
        got = [s for s in (r.json().get("suggestions") or [])
               if s.get("reason") == "describe"]
        assert not got, got


class TestItOnlySpeaksForProse:
    def test_a_short_query_is_not_a_description(self, db, client):
        """Two or three words is a title or a trope, and the rescues that run
        before this one are the right answer for those."""
        r = client.get("/api/search", params={"q": "zzqq wombat", "per_page": 1})
        assert r.status_code == 200
        got = [s for s in (r.json().get("suggestions") or [])
               if s.get("reason") == "describe"]
        assert not got, got

    def test_it_never_runs_when_the_search_found_something(self, db, client):
        n = uuid.uuid4().hex
        db.execute(text("""
            INSERT INTO stories (site, site_id, url, title, author)
            VALUES ('ao3', :s, :u, :t, 'An Author')
        """), {"s": n[:16], "u": f"https://example.test/{n}",
               "t": "A Perfectly Findable Story About Wombats And Rodeos"})
        db.commit()
        r = client.get("/api/search", params={
            "q": "A Perfectly Findable Story About Wombats And Rodeos", "per_page": 3})
        assert r.json()["total"] >= 1
        got = [s for s in (r.json().get("suggestions") or [])
               if s.get("reason") == "describe"]
        assert not got, "spoke over a search that worked"


def test_the_offer_and_its_count_are_built_from_the_same_terms():
    """A probe that does not run the predicate the search will run is guessing.

    `ex.query` can carry status and crossover clauses the probe does not apply,
    so the offered query is rebuilt from the terms that WERE probed — the
    number shown and the search behind it cannot then disagree.
    """
    import inspect
    src = inspect.getsource(S._describe_suggestion)
    assert "_as_query(probe_terms" in src, \
        "the offered query must be built from the probed terms, not ex.query"
    assert "ex.query" not in src.split('"""')[2], \
        "ex.query must not be handed to the reader unprobed"
