"""The shared (L2) search cache.

Why this tier exists is measured, not assumed. With WEB_CONCURRENCY=4 the same
popular query was timed at 11.0s, 9.7s and 6.9s on three successive requests —
each one warming a different uvicorn worker — before settling at 3ms. Four
readers each paid the full disk-bound cost of one search, and it repeated every
time the 120s TTL rolled over. Backing L1 with a table all four workers share
means one request pays and the rest are milliseconds.

The property that matters most here is not speed, it is that the cache CANNOT
break search. It sits on the read path of the most-used endpoint on the site, so
every failure mode — no table, a dead session, a payload written by an older
deploy — has to degrade to "recompute it" and never to a 500. Those tests use a
fake session and run without a database, because they are exactly the cases a
healthy test database would never reproduce.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_cache import (  # noqa: E402
    SCHEMA_VERSION,
    CACHE,
    cache_key,
    shared_get,
    shared_put,
)


class _Boom:
    """A session where everything fails, like a missing table or a dead pool."""

    def __init__(self):
        self.rolled_back = False

    def execute(self, *a, **k):
        raise RuntimeError("database is on fire")

    def commit(self):
        raise RuntimeError("database is on fire")

    def rollback(self):
        self.rolled_back = True


class _BoomRollback(_Boom):
    """Worse: even the rollback fails. Nothing may escape from here either."""

    def rollback(self):
        raise RuntimeError("rollback failed too")


class TestFailureIsAlwaysAMiss:
    def test_read_failure_returns_none(self):
        assert shared_get(_Boom(), "k") is None

    def test_write_failure_is_swallowed(self):
        shared_put(_Boom(), "k", "{}")  # must not raise

    def test_a_failed_read_rolls_the_session_back(self):
        """The caller reuses this session for the real query. A failed statement
        leaves Postgres refusing everything until the transaction is rolled back,
        so without this a cache miss would turn into a failed SEARCH — the cache
        causing the outage it exists to prevent."""
        s = _Boom()
        shared_get(s, "k")
        assert s.rolled_back is True

    def test_even_a_failing_rollback_does_not_escape(self):
        assert shared_get(_BoomRollback(), "k") is None
        shared_put(_BoomRollback(), "k", "{}")


class TestKeying:
    def test_operators_never_share_with_the_public(self):
        """An operator sees delisted rows. Sharing one entry would leak withdrawn
        works into ordinary results — the one failure here an author would care
        about."""
        assert cache_key("q=x", True) != cache_key("q=x", False)

    def test_every_parameter_is_in_the_key(self):
        assert cache_key("q=x&page=1", False) != cache_key("q=x&page=2", False)

    def test_the_same_query_is_the_same_key(self):
        assert cache_key("q=dragon", False) == cache_key("q=dragon", False)


@pytest.mark.usefixtures("db")
class TestRoundTrip:
    def test_a_stored_payload_comes_back(self, db):
        shared_put(db, "rt", '{"hello":"world"}', ttl=60)
        assert shared_get(db, "rt") == '{"hello":"world"}'

    def test_a_missing_key_is_none(self, db):
        assert shared_get(db, "never-written") is None

    def test_an_expired_entry_is_not_served(self, db):
        """Staleness is bounded by the TTL, so an entry past it must be invisible
        even though the row is still sitting there."""
        shared_put(db, "old", '{"a":1}', ttl=-5)
        assert shared_get(db, "old") is None

    def test_rewriting_a_key_replaces_it(self, db):
        shared_put(db, "dup", '{"v":1}', ttl=60)
        shared_put(db, "dup", '{"v":2}', ttl=60)
        assert shared_get(db, "dup") == '{"v":2}'
        from sqlalchemy import text
        n = db.execute(text("SELECT count(*) FROM search_cache_entries "
                            "WHERE key = :k"),
                       {"k": f"{SCHEMA_VERSION}|dup"}).scalar()
        assert n == 1

    def test_a_different_schema_version_does_not_collide(self, db):
        """After a deploy changes the response shape, old entries must not be
        found and handed to a model that no longer matches them."""
        import search_cache
        shared_put(db, "shape", '{"old":true}', ttl=60)
        original = search_cache.SCHEMA_VERSION
        try:
            search_cache.SCHEMA_VERSION = "v-next"
            assert shared_get(db, "shape") is None
        finally:
            search_cache.SCHEMA_VERSION = original
        assert shared_get(db, "shape") == '{"old":true}'


class TestL1:
    def test_l1_still_serves_and_expires(self):
        CACHE.clear()
        CACHE.put("a", {"n": 1})
        assert CACHE.get("a") == {"n": 1}
        assert CACHE.get("absent") is None
        CACHE.clear()
