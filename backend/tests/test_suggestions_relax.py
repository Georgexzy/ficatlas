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


def _crossover_story(db, fandoms, *, is_crossover):
    """`is_crossover` is set explicitly rather than left to the trigger.

    The trigger is installed by `crossover.run`, not by init_db.py, so it may
    or may not exist on a fresh test database — and what is under test here is
    what the FILTER does with the column, not what maintains it.
    """
    n = uuid.uuid4().hex
    db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, fandoms,
                             is_crossover)
        VALUES ('ao3', :sid, :url, :t, 'An Author', CAST(:f AS text[]), :x)
    """), {"sid": n[:16], "url": f"https://example.test/{n}", "t": n[:8],
           "f": list(fandoms), "x": is_crossover})


class TestCrossoverContradiction:
    """Two fandoms and "no crossovers" is not a narrow search, it is an empty one.

    `is_crossover` MEANS a work carrying more than one franchise, so the halves
    of `fandom:A fandom:B xover:exclude` delete each other by construction. The
    reader ticks two fandoms and a "no crossovers" box, and gets a page that
    looks exactly like an index which does not hold the crossover they want.

    From the traffic log, one reader on 2026-09-29:

        fandom:Batman fandom:Danny Phantom site:ao3 xover:exclude      0 works
        fandom:Batman fandom:Danny Phantom site:ao3               5,000 works
    """

    def test_it_offers_to_drop_the_filter_that_emptied_the_search(self, db, client):
        a, b = f"Fandom A {uuid.uuid4().hex[:6]}", f"Fandom B {uuid.uuid4().hex[:6]}"
        for _ in range(25):
            _crossover_story(db, [a, b], is_crossover=True)
        db.commit()
        got = _sug(client, f'fandom:"{a}" fandom:"{b}" xover:exclude', "relax")
        assert got, "an unsatisfiable query got no explanation"
        top = got[0]
        assert top["kind"] == "crossover", got
        assert top["works"] >= 20, got
        # It must keep BOTH fandoms — they are what the reader came for — and
        # drop only the flag that made them contradict each other.
        assert "xover:" not in top["query"], top
        assert a in top["query"] and b in top["query"], top

    def test_it_survives_the_single_term_early_return(self, db, client):
        """Like the length filter and unlike a term: dropping it leaves
        everything the reader searched for intact, so a one-term query still
        gets the offer."""
        a = f"Solo Fandom {uuid.uuid4().hex[:6]}"
        for _ in range(25):
            _crossover_story(db, [a, "Something Else"], is_crossover=True)
        db.commit()
        got = _sug(client, f'fandom:"{a}" xover:exclude', "relax")
        assert [s for s in got if s["kind"] == "crossover"], got

    def test_it_says_nothing_when_the_filter_is_not_the_problem(self, db, client):
        """The ablation puts this filter at a median 8 works recovered, so on
        an ordinary query it is noise. A feature that talks for the sake of it
        gets ignored, and then it is not there on the day it matters."""
        a = f"Quiet Fandom {uuid.uuid4().hex[:6]}"
        for _ in range(25):
            _crossover_story(db, [a], is_crossover=False)
        db.commit()
        got = _sug(client, f'fandom:"{a}" xover:exclude', "relax")
        assert not [s for s in got if s["kind"] == "crossover"], got
