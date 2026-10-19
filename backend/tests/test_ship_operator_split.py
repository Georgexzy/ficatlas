"""`ship:drarry angst` returned nothing, while `relationships=drarry&q=angst`
beside it returned 25. The same query, once typed into the search bar.

`_resolve_or_split` decides where an operator value ends and the free text
begins, and for relationships it asked only `facets` — the table that holds
pairings the way the archives file them, `Draco Malfoy/Harry Potter`. Fandom
does not type that. It types `drarry`, `wolfstar`, `taejin`, and those live in
`ship_aliases`, which is mined from the works rather than listed by anyone.

All 676 of them are single words, so every one is exactly the shape a split
cuts to and none of them can be found through `facets`. So no prefix ever
resolved: the value stayed one relationship named "drarry angst", matched
nobody, and the trailing words never became free text either — the same silent
AND-to-nothing failure as the author one.

The FILTER never had this problem. `ship:drarry` has always returned works,
because the archives store an element like `Drarry - Relationship` and the
filter falls back to a substring match. Only the split was blind to the
vocabulary, which is why half of every such query worked and half did not.

    ship:drarry            ->    315 results   (always worked)
    ship:drarry angst      ->      0 results   (before this file's fix)
    relationships=drarry&q=angst -> 25          (always worked)
"""

import os
import sys
import uuid

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import search as search_mod
from api.search import router as search_router
from db.session import get_db


@pytest.fixture()
def client(db):
    """Through the HTTP layer, because that is where the wiring is.

    As with the author bug, the helper under test is not where the failure
    lived: `_resolve_or_split` was correct for the columns it was asked about,
    and correct is a matter of which vocabulary table the CALLER reaches for.
    Only a request can tell you whether the bar you typed into works.
    """
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _ship_work(db, *, title, relationship="Draco Malfoy/Harry Potter",
               nick_element="Drarry - Relationship", tags=()):
    nonce = uuid.uuid4().hex
    db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, status,
                             tags, relationships)
        VALUES ('ao3', :sid, :url, :t, 'Quillforge', 'complete', :tags, :rels)
    """), {"sid": nonce[:16], "url": f"https://example.test/{nonce}",
           "t": title, "tags": list(tags), "rels": [relationship,
                                                    nick_element] if nick_element else [relationship]})
    db.commit()


@pytest.fixture()
def seeded(db):
    """One pairing under its fandom name, two works — one angsty, one not.

    The nickname row is seeded by hand for the same reason `author_facets` is
    in the author tests: `ship_aliases` is built by the admin refresh against
    the live index, so a value is not in it unless a test puts it there.
    """
    db.execute(text("""
        INSERT INTO ship_aliases (alias, relationship, works, share, built_at)
        VALUES ('drarry', 'Draco Malfoy/Harry Potter', 40, 0.9, now())
        ON CONFLICT (alias) DO UPDATE
            SET relationship = EXCLUDED.relationship
    """))
    # The canonical pairing too, because that is the OTHER vocabulary the split
    # asks: `_facet_exact` reads `facets`, which is built by the same admin
    # refresh and is empty here otherwise.
    #
    # The nickname's own facet is seeded as well, and this is not padding: it
    # is what production holds, since `facets` is built FROM these array
    # elements. It is also what keeps `_vocabulary_absent` honest — without a
    # relationship facet containing "drarry", the vocabulary would PROVE no
    # relationship can hold the word and answer the filter with `false()`
    # instead of the substring match that returns 315 works live.
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
            ('relationship', 'Draco Malfoy/Harry Potter', 40),
            ('relationship', 'Drarry - Relationship', 40)
        ON CONFLICT (kind, value) DO UPDATE SET count = EXCLUDED.count
    """))
    db.commit()
    # Both probes are lru_cached and open their OWN session rather than this
    # test's, so a negative an earlier test in this run already cached would
    # outlive the row just inserted. Cleared rather than slept on.
    search_mod._facet_exact_cached.cache_clear()
    search_mod._facet_contains_cached.cache_clear()
    _ship_work(db, title="An Angst-filled Winter", tags=("Hurt/Comfort",))
    _ship_work(db, title="A Quiet Sunshine", tags=("Fluff",))
    return db


def _total(client, q):
    r = client.get("/api/search", params={"q": q})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["total"], [s["title"] for s in body["results"]]


class TestShipValueIsSplit:
    def test_the_nickname_ends_and_the_rest_becomes_free_text(self, db, client,
                                                              seeded):
        """The regression. Unsplit, the filter is a relationship named
        "drarry angst" and the reader is given an empty page."""
        total, titles = _total(client, "ship:drarry angst")
        assert total == 1, titles
        assert titles == ["An Angst-filled Winter"]

    def test_it_works_the_other_way_round_too(self, db, client, seeded):
        """A second free-text word, to show the cut is at the nickname and not
        simply at the first word of the query."""
        total, titles = _total(client, "ship:drarry sunshine")
        assert total == 1, titles
        assert titles == ["A Quiet Sunshine"]

    def test_the_lookup_is_case_insensitive(self, db, client, seeded):
        """Aliases are stored lowercase and people type capitals."""
        for spelling in ("drarry", "Drarry", "DRARRY"):
            total, titles = _total(client, f"ship:{spelling} angst")
            assert total == 1, (spelling, titles)

    def test_a_single_word_value_is_never_split(self, db, client, seeded):
        """The other half of the contract: `ship:drarry` alone is a ship search,
        not a ship search with an empty free-text arm bolted on."""
        total, _ = _total(client, "ship:drarry")
        assert total == 2

    def test_an_unknown_nickname_is_left_exactly_as_written(self, db, client,
                                                            seeded):
        """An alias nobody has must keep behaving as it did. If the split cut at
        any leading word rather than at a KNOWN nickname, this becomes a free
        text search for "angst" and hands back the angsty work — plausible,
        ranked, and not what was asked for."""
        total, titles = _total(client, "ship:quixotic angst")
        assert total == 0, titles

    def test_a_canonical_pairing_still_splits_on_its_own_vocabulary(self, db,
                                                                    client,
                                                                    seeded):
        """The path that already worked must not regress: a pairing written out
        in full ends at the pairing, via `facets`, and never via the nickname
        table."""
        total, titles = _total(client, "ship:Draco Malfoy/Harry Potter angst")
        assert total == 1, titles
        assert titles == ["An Angst-filled Winter"]


class TestTheSplitDoesNotReachIntoAnotherColumn:
    def test_a_work_without_the_ship_stays_out(self, db, client, seeded):
        """Control: the free text is genuinely ANDed with the ship, not ORed
        beside it. A third work, angsty and of another pairing, must not appear
        — otherwise the assertions above would pass on a search that had lost
        the ship half."""
        _ship_work(db, title="An Angst-filled Summer",
                   relationship="Hermione Granger/Ron Weasley",
                   nick_element="Romione - Relationship")
        total, titles = _total(client, "ship:drarry angst")
        assert total == 1, titles
        assert titles == ["An Angst-filled Winter"]
