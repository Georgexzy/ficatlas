"""A search that found nothing usually has one term too many, not a typo.

The suggestion feature was a spelling rescue and nothing else. Ablating every
component of every query built from a corpus of real fic-finder posts — drop
one part, re-count — says that is answering the wrong question:

    tag                 3,829 works recovered (median)
    word count            972
    character             352
    status                148
    ship                  129
    crossover filter        8
    fandom                  1
    exclusion (-tag)        0

A misspelling announces itself. Over-constraint looks exactly like a thin
index — and the spelling rescue was gated on TYPED TEXT, so the searches most
likely to be over-constrained (the ones built entirely from operators, which is
what every fic-finder link and every sidebar filter produces) got no help at
all. Measured on three real over-constrained searches returning 9, 2 and 0
works: zero suggestions between them.
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
def client(db):
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture()
def tag():
    """A question nothing else has asked — the search cache is per process and
    `conftest` truncates the database between tests."""
    return f"Wombat Rodeo {uuid.uuid4().hex[:8]}"


def _story(db, *, tags=(), rels=(), chars=()):
    n = uuid.uuid4().hex
    db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, tags,
                             relationships, characters)
        VALUES ('ao3', :sid, :url, :t, 'An Author', CAST(:tg AS text[]),
                CAST(:r AS text[]), CAST(:c AS text[]))
    """), {"sid": n[:16], "url": f"https://example.test/{n}", "t": n[:8],
           "tg": list(tags), "r": list(rels), "c": list(chars)})


def _sug(client, q, reason=None):
    r = client.get("/api/search", params={"q": q, "per_page": 1})
    assert r.status_code == 200, r.text
    out = r.json().get("suggestions") or []
    return [s for s in out if reason is None or s.get("reason") == reason]


class TestRelax:
    def test_it_names_the_term_that_is_costing_the_results(self, db, client, tag):
        """Twenty works carry the broad tag and one carries both. The useful
        thing to say is "without the narrow one — 20 works", and to say it
        FIRST, because the reader cannot know which of their terms is expensive."""
        narrow = f"Narrow {uuid.uuid4().hex[:6]}"
        for _ in range(25):
            _story(db, tags=[tag])
        _story(db, tags=[tag, narrow])
        db.commit()
        got = _sug(client, f'tag:"{tag}" tag:"{narrow}"', "relax")
        assert got, "no relax suggestion on an over-constrained search"
        assert got[0]["drops"] == narrow, got
        assert got[0]["works"] >= 20, got
        # And it is runnable as given.
        assert narrow not in got[0]["query"] and tag in got[0]["query"]

    def test_it_fires_without_any_typed_text(self, db, client, tag):
        """The whole point. The spelling rescue is gated on free text, so an
        operator-only query — every fic-finder link, every sidebar filter —
        used to get nothing."""
        narrow = f"Narrow {uuid.uuid4().hex[:6]}"
        for _ in range(25):
            _story(db, tags=[tag])
        _story(db, tags=[tag, narrow])
        db.commit()
        assert _sug(client, f'tag:"{tag}" tag:"{narrow}"', "relax")

    def test_one_term_is_never_relaxed(self, db, client, tag):
        """"Try it without the only thing you searched for" is not a
        suggestion."""
        for _ in range(25):
            _story(db, tags=[tag])
        db.commit()
        assert not _sug(client, f'tag:"{tag}"', "relax")

    def test_a_drop_that_changes_nothing_is_not_offered(self, db, client, tag):
        """A suggestion recovering two works is noise. The floor is what keeps
        the feature from talking for the sake of it."""
        other = f"Other {uuid.uuid4().hex[:6]}"
        for _ in range(3):
            _story(db, tags=[tag, other])
        db.commit()
        assert not _sug(client, f'tag:"{tag}" tag:"{other}"', "relax")


class TestSplitShip:
    def test_a_rarely_filed_pairing_offers_its_two_characters(self, db, client, tag):
        """The archives file a pairing only if somebody wrote it under that
        name, and a rare pairing is a far harder filter than the two people in
        it. Measured on the post that taught this: `Juvia Lockser/Reader` is on
        4 works and returns 2, while works carrying both characters number 26.
        """
        ship, a, b = "A Name/B Name", "A Name", "B Name"
        _story(db, tags=[tag], rels=[ship], chars=[a, b])
        for _ in range(25):
            _story(db, tags=[tag], chars=[a, b])
        db.commit()
        got = _sug(client, f'tag:"{tag}" ship:"{ship}"', "split")
        assert got, "no split suggestion for a starved pairing"
        assert got[0]["works"] >= 20, got
        assert 'char:"A Name"' in got[0]["query"] and "ship:" not in got[0]["query"]


def test_a_suggestion_counts_high_enough_to_be_worth_reading():
    """`_PROBE_CAP` is 20 because the extractor only asks "are there at least
    ten?". A suggestion shows the number to a reader, and every suggestion
    reading "→ 20" tells them nothing and looks broken. Same query, different
    question."""
    from api.search import _PROBE_CAP, _SUGGEST_CAP
    assert _SUGGEST_CAP > _PROBE_CAP * 10
