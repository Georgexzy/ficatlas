"""AO3's dd.chapters is "posted/total", not a number.

Every other stat on an AO3 blurb — words, kudos, hits, comments — is a decimal
with commas and stray labels, so the way to read them is "strip everything that
is not a digit". Applied to chapters, that silently concatenates the two halves:
"70/70" becomes 7070 and "188/188" becomes 188188. Roughly 2,000 rows in the
live index carried a count like that, and since this path runs for works people
actually search for, they skewed heavily towards popular works — which is where
they became visible, on the fandom hub pages.
"""
import pytest

from live_fetch.ao3_live import _parse_chapters_text as parse


class TestFinished:
    def test_the_regression(self):
        """The exact shape that produced 7070."""
        assert parse("70/70") == (70, 70)

    def test_three_digits(self):
        assert parse("188/188") == (188, 188)

    def test_single_chapter(self):
        assert parse("1/1") == (1, 1)

    def test_partially_posted(self):
        assert parse("12/30") == (12, 30)


class TestOngoing:
    def test_unknown_total_is_none(self):
        """"12/?" is a work still being written; the total is not zero, and it is
        not 12 either — it is unknown, and None is the only honest answer."""
        assert parse("12/?") == (12, None)

    def test_one_of_unknown(self):
        assert parse("1/?") == (1, None)


class TestMessyInput:
    def test_commas_are_stripped(self):
        assert parse("1,024/1,024") == (1024, 1024)

    def test_surrounding_whitespace(self):
        assert parse("  12 / 30  ") == (12, 30)

    def test_non_breaking_space(self):
        assert parse("12\xa0/\xa030") == (12, 30)

    def test_empty_is_one_chapter(self):
        """A work with no chapter stat has one chapter, which is what AO3 means
        by omitting it. Never zero — a story with zero chapters does not exist
        and would sort oddly everywhere."""
        assert parse("") == (1, None)
        assert parse(None) == (1, None)

    def test_a_bare_number_has_no_total(self):
        assert parse("5") == (5, None)

    def test_never_returns_zero_posted(self):
        assert parse("0/0")[0] == 1
