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


# ── site: aliases ────────────────────────────────────────────────────────────
#
# stories.site holds exactly `ao3`, `ffnet` and `fictionalley`. The parser used
# to lowercase whatever was typed and pass it through, so any other spelling of
# an archive built a filter no row could satisfy — and an empty result set reads
# as "the index has none of this", not as "that filter was not understood".

def test_site_canonical_values_pass_through():
    assert parse_query("site:ao3").sites == ["ao3"]
    assert parse_query("site:ffnet").sites == ["ffnet"]
    assert parse_query("site:fictionalley").sites == ["fictionalley"]


def test_site_is_case_insensitive():
    assert parse_query("site:AO3").sites == ["ao3"]


def test_site_accepts_the_domain_someone_would_paste():
    assert parse_query("site:fanfiction.net").sites == ["ffnet"]
    assert parse_query("site:archiveofourown.org").sites == ["ao3"]


def test_site_accepts_the_common_abbreviations():
    assert parse_query("site:ffn").sites == ["ffnet"]
    assert parse_query("site:ff.net").sites == ["ffnet"]
    assert parse_query("site:ficalley").sites == ["fictionalley"]


def test_site_accepts_the_digit_zero_misreading_of_ao3():
    assert parse_query("site:a03").sites == ["ao3"]


def test_site_multi_word_name():
    assert parse_query('site:"archive of our own"').sites == ["ao3"]


def test_unknown_site_drops_the_filter_rather_than_matching_nothing():
    """And does not leak the words into the free-text query: searching every
    archive for the real terms beats searching none of them for a site that
    is not in this index."""
    pq = parse_query("site:goodreads harry potter")
    assert pq.sites == []
    assert pq.clean_text == "harry potter"
    assert pq.tokens == []


def test_site_token_shows_the_resolved_archive():
    """The chip the search bar renders comes from the token, so someone who
    typed ff.net can see it landed on ffnet."""
    tok = parse_query("site:ff.net").tokens[0]
    assert tok["key"] == "sites" and tok["value"] == "ffnet"


def test_site_combines_with_other_operators():
    pq = parse_query("site:FF.net fandom:Naruto complete >100k")
    assert pq.sites == ["ffnet"]
    assert pq.fandoms == ["Naruto"]
    assert pq.status == "complete"
    assert pq.word_count_min == 100000
