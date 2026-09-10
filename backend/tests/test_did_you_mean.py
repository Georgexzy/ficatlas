"""Spelling rescue for searches that matched nothing.

DB-backed, because the whole feature is one trigram query against `facets` and
a pure-Python test of it would only assert that a string was formatted.
"""
import pytest
from sqlalchemy import text

from api.search import _did_you_mean, _DYM_MIN_COUNT


@pytest.fixture()
def vocab(db):
    """A small facet vocabulary with the two traps the ranking has to survive."""
    db.execute(text("DELETE FROM facets"))
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('fandom',    'Harry Potter',     686558),
          ('character', 'Harry Potter',     152287),
          ('character', 'Hermione Granger', 102007),
          -- The trap: a MISSPELLING that real works really carry. It matches a
          -- misspelled query at similarity 1.000 and must still lose.
          ('character', 'Hermoine Granger',     55),
          ('character', 'Steve Rogers',     144593),
          ('character', 'Steve Roger',          15),
          ('tag',       'Enemies to Lovers', 63789),
          ('tag',       'Time Travel',        45960)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM facets"))
    db.commit()


def test_a_transposed_letter_still_finds_the_fandom(vocab):
    out = _did_you_mean(vocab, "hsrry potter")
    assert out, "a one-letter typo should be rescued"
    assert out[0].value == "Harry Potter"


@pytest.mark.parametrize("typed,expected", [
    ("hermoine granger",  "Hermione Granger"),
    ("steve rogets",      "Steve Rogers"),
    ("enemies to lovrs",  "Enemies to Lovers"),
    ("time travle",       "Time Travel"),
])
def test_it_corrects_towards_the_spelling_most_works_use(vocab, typed, expected):
    """The count floor is the point.

    Ranking by similarity alone suggests the reader's own mistake back at them:
    `hermoine granger` matches the misspelled facet exactly, and 55 works really
    do spell it that way. similarity * ln(count) picks the 102,007-work
    spelling instead.
    """
    out = _did_you_mean(vocab, typed)
    assert out and out[0].value == expected


def test_a_name_is_offered_once_even_when_several_kinds_carry_it(vocab):
    """"Harry Potter" is both a fandom and a character. Offering the identical
    word twice, differing only by a label, wastes a slot saying one thing."""
    out = _did_you_mean(vocab, "hsrry potter")
    values = [s.value for s in out]
    assert len(values) == len(set(values))


def test_the_suggestion_is_a_runnable_query(vocab):
    out = _did_you_mean(vocab, "hsrry potter")
    assert out[0].query == 'fandom:"Harry Potter"'


def test_nonsense_suggests_nothing(vocab):
    """Below the similarity floor these stop being spellings of the same thing
    and start being coincidences of letters."""
    assert _did_you_mean(vocab, "zzzznonsensequery") == []


def test_a_short_query_is_not_worth_guessing_at(vocab):
    assert _did_you_mean(vocab, "hp") == []
    assert _did_you_mean(vocab, "") == []


def test_rare_facets_are_never_suggested(vocab):
    """Anything under the floor is either a typo somebody committed or too
    obscure to be what the reader meant."""
    out = _did_you_mean(vocab, "steve rogets")
    assert all(s.count >= _DYM_MIN_COUNT for s in out)
    assert "Steve Roger" not in [s.value for s in out]
