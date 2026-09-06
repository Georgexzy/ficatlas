"""Lateral links between hubs — `api/hubs._related`.

Why this exists at all is a measurement, not a preference. The site was two
levels deep with no sideways edges: `/ships` linked all 6,165 ship hubs, each
hub linked 100 story pages, and no hub linked to any other hub. Googlebot
crawls this site 119 times a day and had reached 90 DISTINCT hubs in the whole
retained access log, because a crawler that lands on one pairing from a search
result has nowhere to go but back out. 56% of all referred visits land on a
ship hub, so these are also the pages whose standing is worth passing on.
"""

import os
import sys

import pytest
from sqlalchemy import text as sql_text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.hubs import RELATED_CAP, _related


@pytest.fixture()
def hubs(db):
    db.execute(sql_text(
        "INSERT INTO fandom_hubs (slug, name, variants, work_count) VALUES "
        "('supernatural','Supernatural',ARRAY['Supernatural','Supernatural (TV 2005)'],296901)"))
    for slug, name, variants, n in [
        ("castiel-dean-winchester", "Castiel/Dean Winchester",
         ["Castiel/Dean Winchester", "Dean Winchester/Castiel"], 93724),
        ("castiel-sam-winchester", "Castiel/Sam Winchester", ["Castiel/Sam Winchester"], 4653),
        ("dean-winchester-sam-winchester", "Dean Winchester/Sam Winchester",
         ["Dean Winchester/Sam Winchester"], 31682),
        ("castiel-meg-masters", "Castiel/Meg Masters", ["Castiel/Meg Masters"], 1450),
        ("dean-winchester-reader", "Dean Winchester/Reader", ["Dean Winchester/Reader"], 5918),
    ]:
        db.execute(sql_text(
            "INSERT INTO ship_hubs (slug, name, variants, work_count) "
            "VALUES (:s,:n,:v,:c)"), {"s": slug, "n": name, "v": variants, "c": n})
    db.commit()
    return db


def test_a_pairing_links_up_to_its_fandom(hubs):
    """The single most valuable edge: the only route from a niche pairing into
    a page that has standing. Derived from the works already on the page, so
    there is no stored relation to go stale."""
    rel = _related(hubs, "ship", "castiel-dean-winchester", "Castiel/Dean Winchester",
                   ["Supernatural", "Supernatural", "Good Omens"], [])
    assert rel[0].kind == "fandom" and rel[0].slug == "supernatural"


def test_the_fandom_is_the_modal_one_not_the_first_seen(hubs):
    rel = _related(hubs, "ship", "castiel-dean-winchester", "Castiel/Dean Winchester",
                   ["Good Omens", "Supernatural", "Supernatural"], [])
    assert [r for r in rel if r.kind == "fandom"][0].slug == "supernatural"


def test_both_halves_of_a_pairing_get_siblings(hubs):
    """Taken a slice at a time from each half in turn. Ordering one query by
    work_count filled the whole cap with "Castiel/..." and never reached Dean,
    which answers half the question a reader arrived with."""
    rel = _related(hubs, "ship", "castiel-dean-winchester", "Castiel/Dean Winchester",
                   ["Supernatural"], [])
    slugs = [r.slug for r in rel]
    assert "castiel-sam-winchester" in slugs
    assert "dean-winchester-sam-winchester" in slugs


def test_a_hub_never_links_to_itself(hubs):
    rel = _related(hubs, "ship", "castiel-dean-winchester", "Castiel/Dean Winchester",
                   ["Supernatural"], [])
    assert "castiel-dean-winchester" not in [r.slug for r in rel]


def test_no_duplicates(hubs):
    rel = _related(hubs, "ship", "castiel-dean-winchester", "Castiel/Dean Winchester",
                   ["Supernatural"], [])
    slugs = [r.slug for r in rel]
    assert len(slugs) == len(set(slugs))


def test_the_cap_holds(hubs):
    rel = _related(hubs, "ship", "castiel-dean-winchester", "Castiel/Dean Winchester",
                   ["Supernatural"], [])
    assert len(rel) <= RELATED_CAP


def test_a_fandom_links_down_to_its_pairings(hubs):
    """Fandom hubs cannot outrank AO3 for their own name, so their job is to
    pass a crawler on to the ship hubs, which can."""
    rel = _related(hubs, "fandom", "supernatural", "Supernatural", [],
                   ["Castiel/Dean Winchester", "Castiel/Dean Winchester",
                    "Dean Winchester/Sam Winchester"])
    assert [r.kind for r in rel] == ["ship", "ship"]
    assert rel[0].slug == "castiel-dean-winchester"


def test_an_archive_spelling_still_finds_the_hub(hubs):
    """`variants` holds every spelling the archives use, which is what lets a
    raw relationship string off a work be matched back to its hub."""
    rel = _related(hubs, "fandom", "supernatural", "Supernatural", [],
                   ["Dean Winchester/Castiel"])
    assert rel[0].slug == "castiel-dean-winchester"


def test_an_unknown_subject_yields_nothing_rather_than_raising(hubs):
    assert _related(hubs, "ship", "nope-nothing", "Nobody/Nothing", ["No Such Fandom"], []) == []
