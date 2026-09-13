"""Fandom abbreviations, derived from the naming convention rather than listed.

Readers type `twd`, not "The Walking Dead (TV)". Every term in a search is a
requirement, so an abbreviation the index has never seen matches almost
nothing: `twd self insert` returned NINE works, because `Self-Insert` resolved
and "twd" was left over as a word no story about zombies contains.
"""
import pytest

from fandom_aliases import _initialisms, STOPLIST


@pytest.mark.parametrize("name,expected", [
    # The article COUNTS here, which is why "the" cannot simply be a stopword:
    # dropping it yields `wd`, which nobody types.
    ("The Walking Dead (TV)",                        "twd"),
    # And here every word counts, conjunction included.
    ("A Song of Ice and Fire - George R. R. Martin", "asoiaf"),
    # While here they do not.
    ("Percy Jackson and the Olympians",              "pjo"),
    ("Marvel Cinematic Universe",                    "mcu"),
    ("Game of Thrones (TV)",                         "got"),
    ("Avatar: The Last Airbender",                   "atla"),
])
def test_the_convention_is_not_one_rule(name, expected):
    """Three different treatments of the same articles, all of them what
    readers actually say. So every reading is generated and the vocabulary
    decides which survives."""
    assert expected in _initialisms(name)


def test_both_halves_of_an_ao3_name_are_tried():
    """AO3 writes the original-language name first and English after a pipe,
    and readers use both: this fandom is `bnha` to some and `mha` to others."""
    got = _initialisms("僕のヒーローアカデミア | Boku no Hero Academia | My Hero Academia")
    assert "bnha" in got and "mha" in got


def test_a_one_word_fandom_yields_nothing():
    """`spn` for Supernatural is a NICKNAME, not an initialism — one word
    cannot produce three letters. The rule does not pretend otherwise."""
    assert _initialisms("Supernatural") == set()
    assert _initialisms("Naruto") == set()


@pytest.mark.parametrize("word", ["it", "us", "she", "war", "who"])
def test_abbreviations_that_are_ordinary_words_are_refused(word):
    """`It`, `Us` and `She` are real fandoms. An alias that fires on a common
    English word would hijack any query containing it, which is worse than
    having no alias."""
    assert word in STOPLIST


def test_the_disambiguator_is_not_part_of_the_name():
    """"Batman (Comics)" is Batman; the parenthetical is AO3 telling two
    fandoms apart, not something a reader abbreviates."""
    assert "bc" not in _initialisms("Batman (Comics)")
