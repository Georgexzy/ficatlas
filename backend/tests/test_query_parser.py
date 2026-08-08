"""Unit tests for query_parser.parse_query.

The search bar round-trips filters through this parser, so a parse regression
means the user's typed query and the sidebar agree on different things. Notably
the `series:true/false` operator and exclusions are easy to get subtly wrong.

The exclusion operator is a dash BEFORE the field name (`-fandom:harry`), not
after the colon (`fandom:-harry`, which is treated as a literal value).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query_parser import parse_query


def test_free_text_only():
    pq = parse_query("Harry Potter and the Methods of Rationality")
    assert pq.clean_text == "Harry Potter and the Methods of Rationality"
    assert pq.tokens == []


def test_series_true():
    assert parse_query("series:true").in_series is True


def test_series_false():
    assert parse_query("series:false").in_series is False


def test_in_series_alias():
    assert parse_query("in_series:true").in_series is True


def test_series_does_not_leak_into_free_text():
    # Regression: series:true was once treated as free text and sent as `q`.
    pq = parse_query("space opera series:true")
    assert pq.in_series is True
    assert "series" not in pq.clean_text
    assert pq.clean_text == "space opera"


def test_exclusion_dash_before_field():
    pq = parse_query("-fandom:harry potter")
    assert pq.exc_fandoms == ["harry potter"]
    assert pq.fandoms == []


def test_multi_word_fandom_value():
    pq = parse_query("fandom: Harry Potter - All Media Types")
    assert pq.fandoms == ["Harry Potter - All Media Types"]


def test_word_count_range():
    pq = parse_query("wc:100k-200k")
    assert pq.word_count_min == 100_000
    assert pq.word_count_max == 200_000


def test_mixed_tokens_and_text():
    pq = parse_query("time travel fandom:dramione rating:M")
    assert pq.clean_text == "time travel"
    assert pq.fandoms == ["dramione"]
    assert pq.ratings == ["M"]


def test_rating_alias_canonicalised():
    # "mature" maps to the canonical "M", matching what the sidebar emits.
    assert parse_query("rated: mature").ratings == ["M"]
    assert parse_query("rating: e").ratings == ["E"]
