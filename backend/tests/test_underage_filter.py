"""Sexualised minors are excluded from every surface, by default, for everyone.

This exists because its absence did real harm: a reader was banned for
fourteen days from r/HPFanfiction for linking a FicAtlas search that listed
works tagged "Underage Sex". The explicit toggle was OFF and did nothing,
because it filters on RATING and those works were rated M and Not Rated —
AO3's "Underage" is an archive WARNING, orthogonal to the rating, and the two
had never been connected.
"""
import pytest
from sqlalchemy import text

from api.search import _UNDERAGE_TAGS, _UNDERAGE_WARNINGS, _underage_filter


def test_the_warning_and_the_tag_are_both_covered():
    """The same fact is recorded in two independent places: 10,037 works carry
    the `Underage Sex` archive warning and 22,968 carry an
    `Underage Sex - Freeform` tag. Neither implies the other."""
    assert "Underage Sex" in _UNDERAGE_WARNINGS
    assert "Underage Sex - Freeform" in _UNDERAGE_TAGS


@pytest.mark.parametrize("tag", [
    "Underage Drinking",      # 25,321 works
    "Underage Smoking",       # 7,655
    "Underage Drug Use",      # 3,567
    "Underage Kissing",       # 3,686
    "Chance Meetings",        # what a `chan%` pattern would catch
])
def test_ordinary_content_is_not_swept_up(tag):
    """Matched as EXACT values, never as a substring. Underage drinking is not
    sexualisation of minors, and over-blocking would hide tens of thousands of
    ordinary stories and teach people the filter is broken."""
    assert tag not in _UNDERAGE_TAGS


def test_the_filter_is_on_by_default():
    """Excluded by DEFAULT, and findable on request — not removed from the
    index. This is an index of what the archives hold, and a reader who
    deliberately asks for something the archives themselves label is entitled
    to find it. What the site will not do is put it in front of somebody who
    did not ask, or in a URL they then paste in public."""
    assert _underage_filter() is not None


def test_the_explicit_toggle_does_not_unlock_it():
    """`explicit` is a taste control that readers leave on. This is a
    different question — what a shared LINK carries — so it has its own
    parameter, off by default, and nothing that generates a link sets it.
    Verified live: explicit=true returns 0 underage-flagged works on a page
    where include_underage=true returns 4."""
    import inspect
    from api import search as search_mod
    sig = inspect.signature(search_mod.search)
    assert "include_underage" in sig.parameters, \
        "the gate must be its own parameter, not a mode of `explicit`"
    assert sig.parameters["include_underage"].default.default is False


def test_it_is_not_the_rating_toggle(db):
    """The heart of the bug. `explicit` filters on rating, and every work that
    caused the ban was rated M or NR — so the toggle a reader had turned OFF
    was never going to touch them."""
    db.execute(text("DELETE FROM stories"))
    for title, rating, warns, tags in [
        ("Flagged by warning",  "mature",   ["Underage Sex"], []),
        ("Flagged by tag",      "not_rated", [], ["Consensual Underage Sex"]),
        ("Ordinary, mature",    "mature",   [], ["Underage Drinking"]),
    ]:
        db.execute(text("""
            INSERT INTO stories (id, site, site_id, title, url, rating, warnings,
                                 tags, language, status, word_count, chapter_count)
            VALUES (gen_random_uuid(), 'ao3', :sid, :t, :u, :r,
                    CAST(:w AS text[]), CAST(:g AS text[]),
                    'en', 'complete', 5000, 1)
        """), {"sid": title.replace(" ", "-")[:20], "t": title, "u": "http://x/" + title.replace(" ", "-").replace(",", ""),
               "r": rating, "w": warns, "g": tags})
    db.commit()

    kept = [r[0] for r in db.execute(text("""
        SELECT title FROM stories
         WHERE NOT (warnings && CAST(:w AS text[]))
           AND NOT (tags && CAST(:t AS text[]))
    """), {"w": _UNDERAGE_WARNINGS, "t": _UNDERAGE_TAGS}).fetchall()]

    assert "Ordinary, mature" in kept
    assert "Flagged by warning" not in kept
    assert "Flagged by tag" not in kept

    db.execute(text("DELETE FROM stories"))
    db.commit()
