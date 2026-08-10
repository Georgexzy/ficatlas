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

from live_fetch.ao3_live import _is_complete as done
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


class TestCompletionInference:
    """"36/36" means finished, and this path used not to notice.

    An AO3 blurb shows "Completed:" for a finished work and "Updated:" otherwise
    — and sometimes neither, on a work posted once and never touched. The live
    path read only that label, so 92,350 works sat at n/n while being shown to
    readers as still in progress. The bulk importer and the crawler had always
    used the chapter counter as the second signal; only this one did not, and it
    could not have, because it hardcoded the total to None.
    """
    def test_label_alone_is_enough(self):
        assert done("Completed: 2026-07-13", 5, None)

    def test_all_declared_chapters_posted(self):
        """The case the user spotted: 36/36 is finished, whatever the label says."""
        assert done("Updated: 2026-08-09", 36, 36)

    def test_a_single_chapter_work(self):
        assert done("", 1, 1)

    def test_one_chapter_short_is_not_finished(self):
        """35/36. The whole point of requiring equality."""
        assert not done("Updated: 2026-08-09", 35, 36)

    def test_unknown_total_decides_nothing(self):
        """"36/?" — the author has not said how long it will be."""
        assert not done("Updated: 2026-08-09", 36, None)

    def test_more_posted_than_declared_is_not_completion(self):
        """37/36 is impossible, so it is damaged data rather than a finished
        work — 1,245 rows were in that shape after the chapter-count bug. An
        earlier `>=` would have marked every one of them complete."""
        assert not done("Updated: 2026-08-09", 37, 36)

    def test_the_label_still_wins_over_a_short_count(self):
        """If AO3 says Completed, believe it: an author can finish a work while
        leaving the declared total higher than what they posted."""
        assert done("Completed: 2026-07-13", 35, 36)

    def test_empty_label_and_no_counts(self):
        assert not done("", 1, None)
