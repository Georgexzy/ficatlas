"""Completion status parsed out of an FF.net metadata line.

The point of these is the NEGATIVE case. FF.net prints "Status: Complete" on a
finished work and prints nothing at all on an unfinished one, so the absence of
that marker on a line we successfully parsed is evidence the work is ongoing —
which the enricher used to throw away, leaving 5.3M rows permanently `unknown`
and the "In Progress" filter silently AO3-only.

The risk in recording the negative is claiming it when we did not really parse
anything, so that boundary is what most of these cover.
"""
from ffnet_enrich import parse_ffn_meta


def line(extra=""):
    """A realistic FF.net metadata line; `extra` inserts before the counts."""
    return ("Rated: T - English - Adventure/Drama - Harry P., Hermione G."
            + extra
            + " - Words: 123,456 - Reviews: 789 - Favs: 1,011 - Follows: 1,213"
            " - Updated: 3/4/2019 - Published: 1/2/2015")


def test_labelled_complete_is_recorded():
    assert parse_ffn_meta(line(" - Status: Complete"))["complete"] is True


def test_bare_complete_segment_is_recorded():
    assert parse_ffn_meta(line(" - Complete"))["complete"] is True


def test_no_marker_on_a_parsed_line_means_in_progress():
    """The regression this was written for: previously the key was absent."""
    assert parse_ffn_meta(line())["complete"] is False


def test_nothing_is_claimed_when_no_line_was_parsed():
    """No counts means we did not find a real metadata line.

    Claiming "in progress" here would invent a verdict from a failed parse,
    which is exactly what `unknown` exists to prevent.
    """
    for junk in ("", "404 Not Found", "<html><body>nope</body></html>",
                 "Rated: T - English"):
        out = parse_ffn_meta(junk)
        # Unparseable input returns None outright rather than an empty dict,
        # which is stronger than the guard needs — either way no verdict escapes.
        assert out is None or out.get("complete") is None, junk


def test_counts_without_a_status_still_yield_a_verdict():
    """A minimal but genuine line — counts present, no completion marker."""
    out = parse_ffn_meta("Rated: K - English - Words: 500 - Published: 1/2/2015")
    assert out["complete"] is False
