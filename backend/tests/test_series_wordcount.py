"""The series add-on: three 60k works the author filed as one story are 180k.

A reader who asks for 150k+ wants something to disappear into, and an author
who told one story across three parts has written it. The length is real; it is
simply recorded across rows. Measured on the live index at a 150k floor: 18,077
series of more than one work qualify, holding 158,059 works, and **147,929 of
those are individually shorter** — so without this they cannot be found by
anybody asking for a long read.

Three things these tests hold:

  * it WIDENS and never narrows, so a long standalone still comes back;
  * a ONE-work series is left out, because its total is that work's own length
    and counting it would mean applying the same filter twice;
  * it is OFF by default, because a work that is short on its own is not what
    everybody asking for 150k means.
"""
import os
import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.search import router as search_router
from db.session import get_db

@pytest.fixture()
def tag():
    """A tag nothing else uses, per test.

    The search cache is a process-level L1 in front of the shared table, and
    `conftest` truncates the DATABASE between tests — so a second test asking
    the same question gets the first one's answer, with rows that no longer
    exist. Making each test its own question is cheaper and more honest than
    reaching into the cache."""
    return f"Zebrafish Rodeo {uuid.uuid4().hex[:8]}"


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _story(db, tag, *, title, words, series_total=None):
    nonce = uuid.uuid4().hex
    return db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, word_count,
                             series_total_words, tags)
        VALUES ('ao3', :sid, :url, :t, 'An Author', :w, :stw, ARRAY[:tag])
        RETURNING id
    """), {"sid": nonce[:16], "url": f"https://example.test/{nonce}",
           "t": title, "w": words, "stw": series_total, "tag": tag}).scalar()


def _titles(client, tag, extra=""):
    r = client.get("/api/search", params={
        "q": f'tag:"{tag}" words:>150k{extra}', "per_page": 20})
    assert r.status_code == 200, r.text
    return {row["title"] for row in r.json()["results"]}


class TestSeriesAddOn:
    def test_it_admits_a_short_work_from_a_long_series(self, db, client, tag):
        _story(db, tag, title="Long On Its Own", words=200_000)
        _story(db, tag, title="Part Two Of Three", words=60_000, series_total=180_000)
        db.commit()
        assert _titles(client, tag) == {"Long On Its Own"}
        assert _titles(client, tag, " series:count") == {"Long On Its Own",
                                                    "Part Two Of Three"}

    def test_it_never_removes_anything(self, db, client, tag):
        """OR-ed onto the length filter, never substituted for it. A reader who
        turns the add-on on must not lose the long standalone they already had
        — that is the whole difference between an add-on and a filter."""
        _story(db, tag, title="Long On Its Own", words=200_000)
        _story(db, tag, title="Short And Alone", words=5_000)
        db.commit()
        assert _titles(client, tag, " series:count") == {"Long On Its Own"}

    def test_a_series_shorter_than_the_floor_does_not_qualify(self, db, client, tag):
        _story(db, tag, title="Part Of A Short Series", words=20_000,
               series_total=60_000)
        db.commit()
        assert _titles(client, tag, " series:count") == set()

    def test_it_is_off_by_default(self, db, client, tag):
        """A work that is short on its own is not what everybody asking for
        150k means, so it is offered rather than assumed."""
        _story(db, tag, title="Part Two Of Three", words=60_000, series_total=180_000)
        db.commit()
        assert _titles(client, tag) == set()
        assert _titles(client, tag, " series:count") == {"Part Two Of Three"}


def test_a_one_work_series_is_left_out_of_the_column():
    """The guard is in the SQL, not in the search: 75,622 one-work series are
    real — authors file a standalone in a series, or intend to add more — and
    for those the total is just that one work, so filling the column in would
    mean applying the same filter twice and calling it a feature."""
    from series_wordcount import _FILL_SQL
    assert "member_count > 1" in _FILL_SQL


def test_the_fill_walks_the_index_once():
    """A keyset cursor, not "rows that are currently wrong".

    The first draft drew each batch from rows still needing a change. It
    terminates and it resumes — and the planner satisfies its LIMIT by walking
    ix_series_works_story from the beginning every time, discarding what it has
    already fixed, so batch 21 probes a million rows to find the last fifty
    thousand. Measured at four minutes for the FIRST batch and worsening."""
    from series_wordcount import _FILL_SQL
    assert "sw.story_id > CAST(:after AS uuid)" in _FILL_SQL
    assert "ORDER BY sw.story_id" in _FILL_SQL
