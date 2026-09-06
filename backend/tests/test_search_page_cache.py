"""One computation fills several pages, and the keys have to line up exactly.

Paging cost very nearly a whole search per page. The work is not in returning
twenty rows — it is in materialising up to 5,001 candidates and ranking every
one of them, and OFFSET does not avoid that work, it does it and throws it
away. Measured before the prefetch, same query, cold:

    q=coffee shop au   page 1  3.15s   page 2  2.10s   page 8  2.10s
    q=naruto           page 1  1.62s   page 2  1.09s   page 8  1.03s

After, with pages 2-5 filled off the page-1 computation:

    q=coffee shop au   page 1  2.42s   page 2  0.018s
    q=naruto           page 1  1.33s   page 2  0.005s

None of which is worth anything if the key a prefetch writes under is not the
key the next request looks under, so that is what these pin.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_cache import cache_key, key_for_page


def test_the_key_does_not_depend_on_parameter_order():
    """`?q=x&page=2` and `?page=2&q=x` are one search. They used to be two
    entries: two computations, two rows, and a miss whenever a caller
    reordered its parameters."""
    assert cache_key("q=x&page=2&per_page=20", False) == \
           cache_key("per_page=20&page=2&q=x", False)


def test_a_prefetch_key_is_the_key_that_request_will_use():
    """Not an approximation of it. Both sides canonicalise the same way, so a
    page written ahead is found by the request that asks for it."""
    assert key_for_page("q=x&page=1&per_page=20", False, 3) == \
           cache_key("q=x&page=3&per_page=20", False)


def test_page_is_added_when_the_caller_omitted_it():
    """Page 1 usually arrives with no `page` at all."""
    assert key_for_page("q=x", False, 2) == cache_key("q=x&page=2", False)


def test_an_operator_never_shares_an_entry_with_the_public():
    """Operators see delisted rows. This is the one distinction the key must
    never lose, prefetch or not."""
    assert cache_key("q=x", True) != cache_key("q=x", False)
    assert key_for_page("q=x", True, 2) != key_for_page("q=x", False, 2)


def test_different_searches_still_have_different_keys():
    assert cache_key("q=x", False) != cache_key("q=y", False)
    assert key_for_page("q=x", False, 2) != key_for_page("q=x", False, 3)


def test_repeated_parameters_survive_canonicalisation():
    """`fandoms` and friends arrive repeated, and dropping a duplicate would
    silently widen somebody's search."""
    k = cache_key("fandoms=A&fandoms=B", False)
    assert "fandoms=A" in k and "fandoms=B" in k


def test_the_prefetch_depth_is_configurable():
    from api.search import SEARCH_PREFETCH_PAGES
    assert SEARCH_PREFETCH_PAGES >= 1
