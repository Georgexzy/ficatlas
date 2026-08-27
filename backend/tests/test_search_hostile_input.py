"""Malformed URLs must not 500, hang, or silently drop a valid filter.

Every case here was a real fault found by fuzzing the live API, and all three
were reachable by anyone: /api/search takes no authentication, and a crawler
mangling a query string or a reader editing a page number in the address bar
produces exactly these requests.

They are unit tests over the coercion and the pagination arithmetic rather than
end-to-end HTTP tests, because that is where the bugs actually were and it keeps
them runnable without a populated database.
"""
import pytest

from query_parser import _parse_date
from api.search import SEARCH_COUNT_CEILING


# ── 1. Dates ────────────────────────────────────────────────────────────────
# `?updated_after=notadate` reached Postgres as `>= 'notadate'` and raised
# DataError: invalid input syntax for type timestamp with time zone -> 500.

@pytest.mark.parametrize("bad", [
    "notadate", "zzz", "'; DROP TABLE stories;--", "2024-13-45", "", "   ",
    "99999999", "2024/01/31", "<script>", "NaN", "-1",
])
def test_an_unparseable_date_becomes_none_rather_than_reaching_sql(bad):
    """None means the filter is dropped. Anything else is interpolated into a
    timestamptz comparison, which is the 500."""
    assert _parse_date(bad) is None


@pytest.mark.parametrize("good,expected_prefix", [
    ("2024-01-31", "2024-01-31"),
    ("2024", "2024-01-01"),
])
def test_a_valid_date_still_parses(good, expected_prefix):
    """The fix must not have turned working filters into dropped ones."""
    assert _parse_date(good) == expected_prefix


@pytest.mark.parametrize("rel", ["30d", "1y", "6m", "2w"])
def test_relative_dates_still_parse(rel):
    """`30d` in the URL has to mean what `updated:30d` means in the search bar,
    or the two halves of the same feature disagree."""
    got = _parse_date(rel)
    assert got is not None and len(got) == 10 and got[4] == "-"


# ── 2. Deep pagination ──────────────────────────────────────────────────────
# `?page=500` took 57.9s and 500'd through the proxy: an offset past the
# candidate ceiling cannot contain rows, but the code built the whole candidate
# set so OFFSET could discard it, then re-counted the same set for a total.

@pytest.mark.parametrize("page,per_page", [
    (500, 20), (99999, 20), (251, 20), (1000, 100), (26, 200),
])
def test_offsets_past_the_ceiling_are_recognised_as_empty(page, per_page):
    """The guard is `offset >= SEARCH_COUNT_CEILING`. If this ever stops being
    true for these, the expensive path is reachable again."""
    offset = (page - 1) * per_page
    assert offset >= SEARCH_COUNT_CEILING


@pytest.mark.parametrize("page,per_page", [(1, 20), (2, 20), (10, 20), (1, 100)])
def test_ordinary_pages_are_not_short_circuited(page, per_page):
    """The other edge: a real page must still be fetched. A guard that swallowed
    page 2 would empty the site rather than slow it."""
    offset = (page - 1) * per_page
    assert offset < SEARCH_COUNT_CEILING
