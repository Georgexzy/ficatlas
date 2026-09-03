"""Where an operator value ends when the reader did not say.

`fandom:Harry Potter time travel` is one string, and the parser has to read the
value as running to the end of it — that is the only rule that makes
`tag:slow burn` and `author:Some Long Pen Name` work, and nothing SYNTACTIC
separates those cases. Only the vocabulary can, so the split happens in the API
where the facets table is.

Measured before `_resolve_or_split` existed:

    fandom:Harry Potter time travel   ->  0 results
    fandom:Naruto time travel         ->  1 result
    time travel fandom:Harry Potter   ->  5,000 results

The same search, right only when the operator happened to come last, and silent
about it either way. The one-result case is the worst of the three: it looks
like an answer.

No database here — the vocabulary is injected, because what is being tested is
where the cut goes, not how facets are stored.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# A small stand-in vocabulary. "Some" is present as a SUBSTRING match only,
# which is the trap: an earlier version probed with substring matching and
# happily cut `Some Fandom Nobody Has` down to `Some`.
EXACT = {
    ("fandom", "harry potter"), ("fandom", "naruto"),
    ("character", "hermione granger"),
    ("relationship", "hermione granger/draco malfoy"),
    ("tag", "slow burn"), ("tag", "coffee shop au"),
}
SUBSTRING_ONLY = {"some", "harry", "naruto enemies"}


@pytest.fixture
def split(monkeypatch):
    from api import search as search_mod

    def fake_exact(db, col, term):
        kind = search_mod._FACET_KIND.get(col)
        return (kind, term.strip().lower()) in EXACT

    def fake_variants(db, col, term):
        t = term.strip().lower()
        kind = search_mod._FACET_KIND.get(col)
        if (kind, t) in EXACT:
            return [term]
        return [term] if t in SUBSTRING_ONLY else []

    monkeypatch.setattr(search_mod, "_facet_exact", fake_exact)
    monkeypatch.setattr(search_mod, "_facet_variants", fake_variants)
    return lambda col, val: search_mod._resolve_or_split(None, col, val)


def test_fandom_followed_by_free_text_is_split(split):
    assert split("fandoms", "Harry Potter time travel") == (
        "Harry Potter", ["time travel"])


def test_split_keeps_the_longest_value_that_resolves(split):
    """Not the shortest. `Harry` is a substring match but `Harry Potter` is the
    real fandom, and cutting at the first thing that matched would search the
    wrong one."""
    assert split("fandoms", "Harry Potter time travel")[0] == "Harry Potter"


def test_a_value_that_resolves_whole_is_untouched(split):
    assert split("tags", "slow burn") == ("slow burn", [])
    assert split("tags", "coffee shop au") == ("coffee shop au", [])


def test_single_word_value_is_never_split(split):
    assert split("fandoms", "Naruto") == ("Naruto", [])


def test_unknown_value_is_left_exactly_as_it_was(split):
    """The important negative. Probing with substring matching cut this to
    `Some` + three words of free text, turning an honest empty result into a
    confident wrong one."""
    assert split("fandoms", "Some Fandom Nobody Has") == (
        "Some Fandom Nobody Has", [])


def test_multi_word_free_text_all_comes_back(split):
    assert split("fandoms", "Naruto enemies to lovers") == (
        "Naruto", ["enemies to lovers"])


def test_characters_and_relationships_split_too(split):
    assert split("characters", "Hermione Granger time travel") == (
        "Hermione Granger", ["time travel"])


def test_csv_values_are_handled_per_element(split):
    """Picking two fandoms in the UI sends them comma-separated; only the
    element that needs splitting is touched."""
    val, spill = split("fandoms", "Naruto,Harry Potter time travel")
    assert val == "Naruto,Harry Potter"
    assert spill == ["time travel"]


def test_a_very_long_value_is_left_alone(split):
    """Past a point it is a pen name or a tag, not a fandom plus a phrase."""
    long_val = "a b c d e f g h i j"
    assert split("fandoms", long_val) == (long_val, [])


def test_non_facet_columns_are_ignored(split):
    assert split("author", "Some Long Pen Name") == ("Some Long Pen Name", [])


def test_empty_and_none_are_safe(split):
    assert split("fandoms", None) == (None, [])
    assert split("fandoms", "") == ("", [])
