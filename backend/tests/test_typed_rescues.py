"""Two ways a search returns nothing for a reason the reader cannot see.

Both were found in the traffic log, in real readers' queries, not invented.

A MISTYPED OPERATOR. `shgip:"Regulus Black/James Potter" fandom:"Harry Potter -
J. K. Rowling"` was run twice and returned nothing both times. The parser only
knows `ship:`, so `shgip:` is not an operator at all — it becomes literal text,
every word of it is ANDed against the index, and the result is zero with nothing
on screen to say why. One letter, and no way to tell a typo from an empty index.

A HYPHEN INSIDE A TITLE. `a-rose through time` returned nothing while `a rose
through time` finds the work first. Postgres builds `'a-ros' <2> 'rose' & 'time'`
for the first — a compound token no title contains — against `'rose' & 'time'`
for the second. Readers type the hyphen because that is how a URL slug or a
half-remembered title looks.

Both are OFFERED, never applied, and both are PROBED first: a suggestion that
also returns nothing spends the reader's last bit of patience.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.search import _edit_distance_1, _known_operators  # noqa: E402


class TestEditDistance:
    """One insertion, deletion or substitution — the whole rule."""

    def test_the_typo_that_prompted_this(self):
        assert _edit_distance_1("shgip", "ship")      # one insertion

    def test_a_deletion_and_a_substitution(self):
        assert _edit_distance_1("shp", "ship")        # one deletion
        assert _edit_distance_1("shim", "ship")       # one substitution

    def test_identical_is_not_a_correction(self):
        # Otherwise every correct operator would be "corrected" to itself and
        # the reader would be told to try what they already typed.
        assert not _edit_distance_1("ship", "ship")

    def test_two_edits_is_too_far(self):
        assert not _edit_distance_1("shgpi", "ship")
        assert not _edit_distance_1("tag", "fandom")

    def test_it_does_not_crash_on_edges(self):
        assert not _edit_distance_1("", "")
        assert _edit_distance_1("", "a")
        assert not _edit_distance_1("", "ab")


class TestOperatorTable:
    def test_the_operators_come_from_the_parser(self):
        """Read off FIELD_ALIASES rather than written out again, so a new
        operator cannot be missed here — the drift this codebase keeps having."""
        ops = _known_operators()
        for must in ("ship", "fandom", "tag", "char", "author"):
            assert must in ops, must

    def test_every_operator_is_lowercased(self):
        # The comparison is against a lowercased token, so a capitalised entry
        # in the table would simply never match.
        assert all(o == o.lower() for o in _known_operators())


@pytest.mark.parametrize("bad,good", [("shgip", "ship"), ("shp", "ship"),
                                      ("fandm", "fandom")])
def test_each_typo_has_exactly_one_correction(bad, good):
    """Ambiguity is why the rescue only fires on a single candidate: offering
    two corrections is not a rescue, it is another question."""
    near = [o for o in _known_operators() if _edit_distance_1(bad, o)]
    assert good in near
    assert len(near) == 1, f"{bad} -> {near}"


def test_an_ambiguous_typo_is_left_alone():
    """`tg` is one edit from BOTH `t` and `tag`, so it gets no rescue — and that
    is the rule working, not a gap in it. The table has one-letter aliases, so
    short typos are ambiguous more often than they look; guessing between two
    would be worse than saying nothing, because a wrong correction reads as the
    site insisting the reader meant something they did not."""
    near = [o for o in _known_operators() if _edit_distance_1("tg", o)]
    assert len(near) > 1, near
