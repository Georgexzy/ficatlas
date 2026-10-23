"""A pasted work link is a lookup, not a browse — the adult tier must not hide it.

The measured bug (2026-10-23): a reader pasted
`https://archiveofourown.org/works/77535806`, the link resolved to a row that
sat in the table with `gate_adult` set, and the search reported zero works.
Four filters did this independently — the adult gate's column/warning/tag
predicates, the E-rating predicate, and the `ratings=G,T,M,NR` the search bar
sends automatically with the explicit toggle off. This file pins the exemption
and, just as importantly, pins what it does NOT exempt: the underage tier and a
filter the reader chose deliberately.

DB-backed, because the feature is a filter list assembled inside `search()`.
"""
import inspect

import pytest
from fastapi import Response
from sqlalchemy import text

from api import search as S
from api.search import search


@pytest.fixture()
def works(db):
    """Three rows: an adult-gated one, an E-rated one, and a plain one.

    The plain work is what the other two are compared against — it must stay
    findable and stay findable *through the same path*, so a test that passes
    because the fixture is broken cannot masquerade as a gate test.
    """
    rows = {
        "adult":  _insert(db, site_id="900001", title="The Adult Gated Work",
                          gate_adult=True),
        "erated": _insert(db, site_id="900002", title="The Explicit Work",
                          rating="explicit"),
        "plain":  _insert(db, site_id="900003", title="The Plain Work"),
    }
    db.commit()
    return rows


def _insert(db, *, site_id, title, **extra):
    cols = {"site": "ao3", "site_id": site_id, "title": title,
            "url": f"https://archiveofourown.org/works/{site_id}", **extra}
    collist = ", ".join(cols)
    placeholders = ", ".join(f":{k}" for k in cols)
    return db.execute(text(
        f"INSERT INTO stories ({collist}) VALUES ({placeholders}) RETURNING id"
    ), cols).scalar()


def _search(db, **over):
    """Call the real endpoint with only the params under test supplied.

    Every other parameter is a FastAPI `Query(...)` object by default, which a
    direct Python call would receive as a truthy sentinel rather than its value.
    Resolving each default from the signature keeps the call honest: it is the
    endpoint's own filter assembly running, not a re-implementation of it.
    """
    kw = {}
    for name, p in inspect.signature(search).parameters.items():
        if name in ("db", "viewer", "request", "response"):
            continue
        d = p.default
        kw[name] = getattr(d, "default", d)
    kw.update(over)
    return search(db=db, response=Response(), request=None, viewer=None, **kw)


# The synthetic set the search bar sends with the explicit toggle off. It is
# what arrives alongside a real reader's pasted link, so the tests send it too.
_TOGGLE_OFF = {"ratings": "G,T,M,NR", "explicit": False}


def test_a_pasted_link_finds_an_adult_gated_work(works, db):
    out = _search(db, q="https://archiveofourown.org/works/900001", **_TOGGLE_OFF)
    assert out.total == 1, "a work we hold must not be reported as missing"
    assert [w.id for w in out.results] == [str(works["adult"])]


def test_a_pasted_link_finds_an_explicit_rated_work(works, db):
    # The ratings exemption, not just the gate column: the bar sends
    # `ratings=G,T,M,NR` with every toggle-off search, which would exclude an
    # E-rated work even with `gate_adult` off.
    out = _search(db, q="https://archiveofourown.org/works/900002", **_TOGGLE_OFF)
    assert [w.id for w in out.results] == [str(works["erated"])]


def test_a_normal_search_still_hides_both(works, db):
    # The gate is still real for browsing — the exemption is the pasted link,
    # not the setting. Same rows, same endpoint, text search instead of a link.
    for work_id in (works["adult"], works["erated"]):
        out = _search(db, q=_title(db, work_id), **_TOGGLE_OFF)
        assert out.total == 0, "turning the gate off for links must not open it for searches"


def test_a_deliberate_rating_filter_still_applies_to_a_link(works, db):
    # A reader who ticked Teen alone asked for Teen; that is a choice, not the
    # toggle restated, so it still excludes the not-rated work. Only the
    # synthetic all-but-explicit set is exempted.
    out = _search(db, q="https://archiveofourown.org/works/900001",
                  ratings="T", explicit=False)
    assert out.total == 0


def test_the_underage_tier_is_not_exempt(works, db):
    # The policy line holds: a pasted link does not reach an underage-gated
    # work. It is a setting a reader turns on deliberately, not a taste control.
    underage = _insert(db, site_id="900004", title="The Underage Work",
                       gate_underage=True)
    db.commit()
    out = _search(db, q="https://archiveofourown.org/works/900004", **_TOGGLE_OFF)
    assert out.total == 0
    # And it stays reachable for a reader who did turn the setting on.
    out = _search(db, q="https://archiveofourown.org/works/900004",
                  include_underage=True, **_TOGGLE_OFF)
    assert [w.id for w in out.results] == [str(underage)]


def test_operators_after_the_link_still_filter(works, db):
    # The tail of a pasted link is the reader's own narrowing and keeps its
    # meaning — an operator on the same line must not be swallowed by the
    # exemption. `fandom:` is one the merge list really applies (see the note
    # below), and the plain work carries no fandoms, so it excludes the work.
    out = _search(db, q="https://archiveofourown.org/works/900003 fandom:Not This Fandom",
                  **_TOGGLE_OFF)
    assert out.total == 0


# NOT `cat:M/M`, which was the first choice here and is dropped for a reason
# unrelated to this file: `parsed_to_search_params` emits `categories`
# (query_parser.py:534) but the merge list in `search()` never copies it into
# the parameter, and `buildParams` sends only the sidebar's chips — so a typed
# `cat:` operator is discarded at both ends, for a pasted link or not. That is
# its own bug with its own fix; it is written down here so the next person does
# not rediscover it by watching this test fail.


def _title(db, story_id):
    return db.execute(text("SELECT title FROM stories WHERE id = :i"),
                      {"i": story_id}).scalar()
