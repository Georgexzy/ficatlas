"""The "N works hidden" notice must describe the tier that hid them.

The count answered "how many E-RATED works are hidden" while the filter applied
a whole TIER — the E rating, the `gate_adult` column and the adult tag arrays.
So a work rated Teen and tagged `Dead Dove: Do Not Eat` was hidden from the
results and missing from the number that explains why the results are short,
and the notice said "rated explicit" about works that were not.

Measured on the live index before the fix: `ship:"Juvia Lockser/Reader"`
returned 2 works with `hidden_explicit = 0`, while turning the tier off
returned 7. After: 2 + 5 = 7.
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
    """A question nothing else has asked.

    The search cache is a process-level L1 in front of a shared table and
    `conftest` truncates the DATABASE between tests, so a second test asking
    the same question gets the first one's answer — with rows that no longer
    exist. Caught here as `hidden_explicit == 3` on a test that seeds two
    works.
    """
    return f"Wombat Rodeo {uuid.uuid4().hex[:8]}"


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(search_router, prefix="/api/search")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _story(db, tag, *, title, rating="teen", tags=()):
    nonce = uuid.uuid4().hex
    db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, rating, tags)
        VALUES ('ao3', :sid, :url, :t, 'An Author', :r, CAST(:tg AS text[]))
    """), {"sid": nonce[:16], "url": f"https://example.test/{nonce}",
           "t": title, "r": rating, "tg": list(tags) + [tag]})
    db.commit()


def _search(client, tag, **kw):
    params = {"q": f'tag:"{tag}"', "per_page": 50}
    params.update(kw)
    r = client.get("/api/search", params=params)
    assert r.status_code == 200, r.text
    return r.json()


class TestHiddenAdultCount:
    def test_a_teen_rated_dead_dove_work_is_counted(self, db, client, tag):
        """The case the old count missed entirely: hidden by the TAG, invisible
        to a count built on the rating."""
        _story(db, tag, title="Plain", rating="teen")
        _story(db, tag, title="Dove", rating="teen", tags=["Dead Dove: Do Not Eat"])
        shown = _search(client, tag)
        assert shown["total"] == 1
        assert shown["hidden_explicit"] == 1, shown

    def test_the_count_explains_the_whole_gap(self, db, client, tag):
        """total + hidden == what the tier-off search returns. If the notice
        cannot be added to the result count, it is not explaining it."""
        _story(db, tag, title="Plain", rating="teen")
        _story(db, tag, title="Rated", rating="explicit")
        _story(db, tag, title="Dove", rating="teen", tags=["Dead Dove: Do Not Eat"])
        _story(db, tag, title="Noncon", rating="teen", tags=["Rape/Non-con Elements"])
        off = _search(client, tag)
        on = _search(client, tag, explicit="true")
        assert off["total"] + off["hidden_explicit"] == on["total"], (off, on)

    def test_tier_one_is_neither_dropped_nor_counted(self, db, client, tag):
        """A reader is not told how many works were hidden for sexualised
        minors. That is a setting they turn on deliberately, not a number that
        invites curiosity — and the count must not quietly reveal it."""
        _story(db, tag, title="Plain", rating="teen")
        _story(db, tag, title="Gated", rating="teen", tags=["Underage Sex - Freeform"])
        shown = _search(client, tag)
        assert shown["total"] == 1
        assert shown["hidden_explicit"] == 0, shown

    def test_turning_the_adult_tier_off_does_not_reveal_tier_one(self, db, client, tag):
        _story(db, tag, title="Plain", rating="teen")
        _story(db, tag, title="Gated", rating="teen", tags=["Underage Sex - Freeform"])
        on = _search(client, tag, explicit="true")
        assert {r["title"] for r in on["results"]} == {"Plain"}
