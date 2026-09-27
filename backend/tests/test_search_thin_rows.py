"""A row with nothing to show for itself sorts last — and is never hidden.

57% of the index has no summary, and it is not a crawl failure: 12.9M AO3 rows
came from a bulk metadata dump whose schema has no summary field at all. Those
same rows carry no engagement figure either, so `pop` is 0 for nearly all of
them and `text_rank` barely separates them. The order AMONG them was therefore
arbitrary, and a reader paging through results met works they could not judge
interleaved with ones they could.

The fix has to be a DEMOTION and not a filter. Measured on the live index after
it, `fandoms=Naruto` sorted by relevance:

    page 1     0 of 20 without a summary
    page 248  18 of 20
    page 250  20 of 20

with the total unchanged at 5,000. That shape — pushed to the back, still
reachable — is what these tests pin.
"""
import re

from api.search import THIN_PENALTY, _thin


def _sql(expr) -> str:
    return str(expr.compile(compile_kwargs={"literal_binds": True}))


def test_a_row_with_no_summary_is_thin():
    """1.0 for thin, 0.0 for complete, so it can be subtracted from a score."""
    sql = _sql(_thin())
    assert "summary" in sql
    assert "1.0" in sql and "0.0" in sql


def test_thinness_is_measured_on_the_summary_only():
    """A truncated TITLE is excluded from search by `_BROKEN_TITLE_TAIL`, not
    demoted, so re-testing that rule here would match nothing that got this
    far. The titles it deliberately does not catch are indistinguishable from
    real ones by any rule that does not also hide real ones."""
    sql = _sql(_thin()).lower()
    assert "title" not in sql


def test_whitespace_is_not_a_summary():
    assert "trim" in _sql(_thin()).lower()


def test_the_penalty_cannot_outweigh_an_exact_title_match():
    """The failure this must never cause is a work becoming unfindable. An
    exact title match scores `w_exact` = 4.0 on its own, so a work named
    exactly what was typed outranks the penalty several times over however
    little else it has."""
    assert THIN_PENALTY < 4.0


def test_the_penalty_cannot_outweigh_a_real_title_match():
    """This bound used to be `<= 1.0`, on the reasoning that `pop` is 0..1
    scaled by w_pop (1.0 on a title query, 3.5 on a category one) and that the
    penalty should settle ties among the flat tail without reordering works
    readers have actually separated.

    Sound reasoning, and the data does not support the fear. Missing summaries
    are almost perfectly confined to works with no recorded readership at all:

        kudos > 5000          4 of      6,326    0.1%
        kudos 1001-5000     143 of     76,255    0.2%
        kudos 101-1000    7,035 of    819,349    0.9%
        kudos 1-100      72,605 of  1,804,283    4.0%
        kudos 0 or none  11,357,511 of 11,502,541   98.7%

    11.36M of the 11.5M summary-less rows have zero kudos. Well-read works are
    re-encountered and enriched, so what this penalty demotes is the unread
    tail -- not anything readers have separated. All the Young Dudes, the
    most-read work here at 322,055 kudos, has a summary.

    The 143 works in the 1k-5k band are the real cost of this, and they are
    0.2% of that band. That is the trade, stated plainly.

    So the penalty was raised to be decisive between otherwise comparable works,
    which is what it is for: 81.2% of AO3 rows here have no summary, and a
    result page of bare titles is what a first-time visitor judges the site by.

    What it must still not do is beat a genuine match. w_title is 3.0, so a
    summary-less work that really is what the reader typed still wins -- a thin
    row that matches beats a rich row that does not.
    """
    assert 1.0 <= THIN_PENALTY < 3.0


def test_the_penalty_is_configurable_without_a_deploy():
    """Same reason SEARCH_TROPE_TAGS and SEARCH_SHIP_ALIASES have switches:
    this sits on the ranking of every free-text search."""
    import os

    import importlib
    import api.search as search
    os.environ["SEARCH_THIN_PENALTY"] = "0.0"
    try:
        importlib.reload(search)
        assert search.THIN_PENALTY == 0.0
    finally:
        del os.environ["SEARCH_THIN_PENALTY"]
        importlib.reload(search)
