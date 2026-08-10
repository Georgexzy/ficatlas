"""The search response cache.

The failure that would actually matter is not staleness — it is serving an
operator's result set to the public. Operators see delisted rows: works whose
authors asked to be removed from the index. One shared cache entry would put
those back in front of everyone, and it would look like a caching win rather
than a leak. Most of these tests exist for that.
"""
import time

import pytest

from search_cache import _TTLCache, cache_key


class TestCacheKey:
    def test_operator_and_public_never_share_an_entry(self):
        """The one that matters. Delisted works are visible to operators only."""
        assert cache_key("q=harry", True) != cache_key("q=harry", False)

    def test_same_viewer_and_query_is_the_same_entry(self):
        assert cache_key("q=harry&page=2", False) == cache_key("q=harry&page=2", False)

    def test_different_queries_are_different_entries(self):
        assert cache_key("q=harry", False) != cache_key("q=naruto", False)

    def test_every_parameter_is_part_of_the_key(self):
        """Filters, paging and sort all change the result set, so a key that
        ignored any of them would serve the wrong page."""
        base = cache_key("q=x", False)
        for other in ("q=x&page=2", "q=x&sort=kudos", "q=x&ratings=general",
                      "q=x&per_page=50", "q=x&exclude_tags=angst"):
            assert cache_key(other, False) != base


class TestTTLCache:
    def test_stores_and_returns(self):
        c = _TTLCache()
        c.put("k", {"total": 1})
        assert c.get("k") == {"total": 1}

    def test_missing_key_is_a_miss(self):
        assert _TTLCache().get("nope") is None

    def test_entries_expire(self):
        c = _TTLCache(ttl=0)
        c.put("k", "v")
        time.sleep(0.01)
        assert c.get("k") is None

    def test_unexpired_entries_survive(self):
        c = _TTLCache(ttl=60)
        c.put("k", "v")
        assert c.get("k") == "v"

    def test_lru_bound_is_enforced(self):
        """Bounded on purpose: this runs in a container with a memory limit, and
        an unbounded cache of search responses is a slow leak."""
        c = _TTLCache(max_entries=3)
        for i in range(5):
            c.put(f"k{i}", i)
        assert c.stats()["entries"] == 3

    def test_least_recently_used_is_the_one_dropped(self):
        c = _TTLCache(max_entries=2)
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")          # touch a, so b is now least recently used
        c.put("c", 3)
        assert c.get("a") == 1
        assert c.get("b") is None

    def test_hit_and_miss_counts(self):
        c = _TTLCache()
        c.put("k", "v")
        c.get("k"); c.get("k"); c.get("absent")
        s = c.stats()
        assert (s["hits"], s["misses"]) == (2, 1)
        assert s["hit_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_clear_empties_it(self):
        c = _TTLCache()
        c.put("k", "v")
        c.clear()
        assert c.get("k") is None

    def test_expiry_is_per_entry_not_global(self):
        c = _TTLCache(ttl=60)
        c.put("old", 1)
        c._data["old"] = (time.time() - 1, 1)     # force just this one to expire
        c.put("new", 2)
        assert c.get("old") is None
        assert c.get("new") == 2
