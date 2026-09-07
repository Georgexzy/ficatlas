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


# ── serialise_filters: the inverse, used by the traffic log ───────────────────
#
# The property that matters is the ROUND TRIP. What gets written into the search
# log is what the search bar shows, so a recorded row can be pasted back in and
# run again — and the only way that stays true is if parse_query() reads back
# what serialise_filters() writes. Every test here checks both directions.

from starlette.datastructures import QueryParams  # noqa: E402
from query_parser import serialise_filters  # noqa: E402


def _round(qs: str):
    """Serialise these params, then parse the result back."""
    text = serialise_filters(QueryParams(qs))
    return text, parse_query(text)


def test_filter_only_search_serialises_to_the_bars_syntax():
    """The case the traffic log was blind to: no free text at all."""
    text, pq = _round("fandoms=Naruto")
    assert text == "fandom:Naruto"
    assert pq.fandoms == ["Naruto"]


def test_a_browse_with_nothing_narrowing_it_serialises_to_nothing():
    """Empty is the signal not to record. An unfiltered browse is not a query
    anybody can act on, and "" would collapse every one into a single row."""
    assert serialise_filters(QueryParams("sort=popularity_desc&page=2")) == ""
    assert serialise_filters(QueryParams("")) == ""
    assert serialise_filters(QueryParams("q=drarry")) == ""   # the text half is not ours


def test_several_filters_round_trip_together():
    text, pq = _round("tags=Fluff&sites=ffnet&word_count_min=100000&status=complete")
    assert pq.tags == ["Fluff"]
    assert pq.sites == ["ffnet"]
    assert pq.word_count_min == 100000
    assert pq.status == "complete"


def test_comma_joined_and_repeated_values_both_work():
    """The frontend sends ?tags=a,b; links in the wild send ?tags=a&tags=b."""
    assert _round("fandoms=Harry Potter,Naruto")[1].fandoms == ["Harry Potter", "Naruto"]
    assert _round("fandoms=Harry Potter&fandoms=Naruto")[1].fandoms == ["Harry Potter", "Naruto"]


def test_a_full_rating_set_is_not_a_narrowing():
    """Every rating selected IS the default. Spelling it out would put four
    operators in front of every recorded search."""
    assert serialise_filters(QueryParams("ratings=G,T,M,NR")) == ""
    assert serialise_filters(QueryParams("ratings=M")) == "rating:M"


def test_all_three_sites_is_not_a_narrowing():
    assert serialise_filters(QueryParams("sites=ao3,ffnet,fictionalley")) == ""
    assert serialise_filters(QueryParams("sites=ao3,ffnet")) == "site:ao3 site:ffnet"


def test_a_value_that_would_break_the_parser_is_quoted():
    """A fandom ending in a shorthand word would lose it on the way back."""
    text, pq = _round("fandoms=Everything Is Complete")
    assert text == 'fandom:"Everything Is Complete"'
    assert pq.fandoms == ["Everything Is Complete"]


def test_a_ship_with_a_slash_survives():
    """Pairings are the commonest filter and carry a slash; nothing may eat it."""
    text, pq = _round("relationships=Draco Malfoy/Harry Potter")
    assert pq.relationships == ["Draco Malfoy/Harry Potter"]


def test_exclusions_keep_their_minus():
    text, pq = _round("exclude_tags=Angst")
    assert text == "-tag:Angst"
    assert pq.exc_tags == ["Angst"]


def test_word_counts_use_the_suffixes_the_parser_understands():
    """Raw digits do not round-trip: the parser reads k/m only."""
    assert serialise_filters(QueryParams("word_count_min=50000")) == "words:>50k"
    assert serialise_filters(QueryParams("word_count_max=1000000")) == "words:<1m"
    assert _round("word_count_min=100000&word_count_max=200000")[1].word_count_min == 100000
