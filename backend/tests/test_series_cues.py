"""Unit tests for series_cues.parse_named position extraction.

Regression: a work marked "BOOK FOUR OF THE ELIZABETH KANE SERIES" (cardinal
word AFTER "book") got no position and sorted last in its series, because the
parser only handled "SECOND BOOK IN..." (ordinal first) and "Book 2"
(digits/roman). The cardinal-after-marker form is the exact shape that broke.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from series_cues import parse_named


def test_book_cardinal_after_marker():
    got = parse_named("BOOK FOUR OF THE ELIZABETH KANE SERIES")
    assert got is not None
    assert got["position"] == 4


def test_book_ordinal_before_marker():
    got = parse_named("THIRD BOOK IN THE ELIZABETH KANE SERIES")
    assert got is not None
    assert got["position"] == 3


def test_part_word_number_after_marker():
    got = parse_named("Part two of the Dangerverse")
    assert got is not None
    assert got["position"] == 2


def test_book_roman_after_marker():
    got = parse_named("Book II of the Chronicles")
    assert got is not None
    assert got["position"] == 2


def test_named_ordinal_first():
    got = parse_named("third in the Facing the Future series")
    assert got is not None
    assert got["position"] == 3


def test_no_false_positive_bare_cardinal():
    # The cardinal must be followed by "of/in the X series" to count; a bare
    # number in unrelated prose must not be read as a position.
    assert parse_named("one of my favourite fics") is None
    assert parse_named("four friends go on an adventure") is None
    assert parse_named("she won two gold medals") is None
    assert parse_named("a standalone oneshot") is None
