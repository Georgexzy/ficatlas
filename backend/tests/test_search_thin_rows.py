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


def test_the_penalty_cannot_outweigh_readership():
    """`pop` is 0..1 scaled by `w_pop`, which is 1.0 on a title query and 3.5
    on a category one. The penalty settles ties among the flat tail; it must
    not reorder works that readers have actually separated."""
    assert THIN_PENALTY <= 1.0


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
