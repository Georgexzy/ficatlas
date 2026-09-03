"""A short-lived cache for search responses.

Why this and not more hardware
------------------------------
Load testing put search at roughly 0.5 requests a second while every other
endpoint ran in the hundreds. The cause is not CPU: raising the API from one
worker process to four moved search not at all. It is disk. The database is
39GB against ~1.5GB of shared_buffers and a page cache competing with a desktop,
and under search load the buffer hit ratio measured 47.5% — half of all block
accesses going to the NVMe. The GIN index is fine; it returns in ~100ms once the
blocks are resident, and seconds when they are not.

Nothing in the query is going to fix that ratio. Either the working set gets
smaller or the same query stops being asked repeatedly, and the second is free.

Search traffic is not uniform. A public site's queries follow a Zipf-like
distribution — a small number of fandoms, ships and tropes account for a large
share of everything typed — and those are exactly the queries whose blocks are
worth keeping. A cache turns the popular half of the traffic into memory hits
and leaves the disk for the long tail.

Correctness
-----------
Two things must be in the key or the cache is a bug:

  * the full set of query parameters, since every filter changes the result;
  * whether the viewer is an operator, because operators see delisted rows that
    nobody else does. Sharing one entry between an admin and the public would
    leak withdrawn works into ordinary results — the one failure here that would
    matter to an author rather than merely being stale.

Staleness is bounded by TTL and is the acceptable half of the trade: the index
gains rows continuously but no individual search becomes wrong within a couple
of minutes. Anything with an editorial effect — a takedown, a delisting — is not
served from here at all, because those change what an operator sees and
operators are keyed separately, and because two minutes is shorter than any
human notices.

Two tiers, and why
------------------
L1 is per process. With WEB_CONCURRENCY=4 that is four caches, and measuring it
on this box showed what that actually costs: the same popular query took 11.0s,
9.7s and 6.9s on three successive requests — each warming a different worker —
before settling at 3ms. Four readers each pay the full disk-bound price of one
search, and it repeats every time the TTL rolls.

So L1 is backed by L2, an UNLOGGED table in the database every worker is already
connected to (DDL in init_db.py). A miss in L1 costs one indexed lookup on a
small table, ~1ms, against ~10s to recompute. The first worker to run a query
warms it for all of them, and the entry outlives a worker restart.

L2 is deliberately not Redis. The bottleneck here is that 36GB of database is
competing for a page cache it does not fit in; adding a second service to hold
data that is by definition recomputable spends the exact resource that is scarce.

Failure is always a miss, never an error. Every L2 path is wrapped: if the table
is missing, the write fails, or the payload no longer parses after a deploy
changed the response shape, the search recomputes. A cache that can take the
endpoint down with it is worse than no cache.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

log = logging.getLogger(__name__)

# Two minutes. Long enough that a burst of people searching the same popular
# fandom collapses onto one query; short enough that a reader refining a search
# never sees a result set that predates their last edit in any way they could
# notice.
TTL_SECONDS = 120

# Entries, not bytes. A search response is ~20-40KB of JSON, so this is roughly
# 20-40MB per worker at capacity, against a 1.4GB container limit.
MAX_ENTRIES = 800


class _TTLCache:
    """LRU with expiry. Small enough to be obvious; locked because request
    handlers run in a threadpool."""

    def __init__(self, max_entries: int = MAX_ENTRIES, ttl: int = TTL_SECONDS):
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max = max_entries
        self._ttl = ttl
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires, value = item
            if expires < now:
                # Expired entries are dropped on read rather than by a sweeper:
                # anything never read again is evicted by the LRU bound anyway.
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time() + self._ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }


CACHE = _TTLCache()


def cache_key(query_string: str, is_operator: bool) -> str:
    """The full query plus the viewer's visibility.

    Operators see delisted rows, so they must never share an entry with the
    public — see the module docstring.
    """
    return f"{'op' if is_operator else 'pub'}|{query_string}"


# --- L2: shared across workers -------------------------------------------------

# Bumped when the shape of a cached response changes. An entry written by the
# old code is then simply not found rather than being deserialised into a model
# that no longer matches it — which is the difference between a deploy that
# quietly serves malformed results for two minutes and one that does not.
# v2: SearchResponse gained `hidden_explicit`.
# v3: free text can now resolve a ship nickname to its canonical pairing,
# so the same query returns a different (wider) set than a v2 entry holds.
# v4: relevance ranks over a FIELD-WEIGHTED tsvector. The response shape is
# unchanged — the ORDER inside it is not, and a cached v3 entry holds the old
# ordering. Worth stating plainly because the rule above says "shape": a
# ranking change invalidates the cache just as completely, and skipping this
# bump is invisible. It cost half an hour here, verifying a fix against cached
# results and concluding it had not worked.
# v5: an operator value that ran on into the free text is now split back apart
# against the vocabulary, so `fandom:Harry Potter time travel` returns 5,000
# works instead of 0. Same URL, same shape, different results — which is
# exactly the case the v4 note above was written about, and it caught this one.
SCHEMA_VERSION = "v5"

# Expired rows are swept probabilistically on write rather than by a scheduled
# job: 1 write in 200 pays for the cleanup, which at any real request rate keeps
# the table to roughly its live set without anything needing to own a cron.
_SWEEP_EVERY = 200
_writes = 0
_writes_lock = threading.Lock()


def shared_get(db, key: str) -> Optional[str]:
    """The cached JSON body, or None. Never raises."""
    return (shared_get_with_ttl(db, key) or (None, 0))[0]


def shared_get_with_ttl(db, key: str) -> Optional[tuple[str, int]]:
    """The cached body AND how long it has left, or None.

    The remaining life matters to the caller because it is what the response
    should advertise. Answering a cache hit with the floor TTL throws away the
    fact that the underlying search was expensive -- and popular searches are
    served from cache almost every time, so the expensive ones would advertise
    the floor permanently and the edge would re-ask every two minutes.
    """
    try:
        from sqlalchemy import text
        row = db.execute(text(
            "SELECT payload, GREATEST(0, round(extract(epoch from (expires_at - now()))))::int "
            "  FROM search_cache_entries "
            " WHERE key = :k AND expires_at > now()"
        ), {"k": f"{SCHEMA_VERSION}|{key}"}).first()
        return (row[0], row[1]) if row else None
    except Exception:
        # Rolled back so the caller's session is still usable for the real
        # query. Without this a cache miss caused by a broken table would
        # poison the transaction and turn into a failed search.
        try:
            db.rollback()
        except Exception:
            pass
        log.debug("shared search cache read failed", exc_info=True)
        return None


def shared_put(db, key: str, payload: str, ttl: int = TTL_SECONDS) -> None:
    """Store a rendered response body. Never raises."""
    global _writes
    try:
        from sqlalchemy import text
        db.execute(text("""
            INSERT INTO search_cache_entries (key, payload, expires_at)
            VALUES (:k, :p, now() + make_interval(secs => :t))
            ON CONFLICT (key) DO UPDATE
               SET payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at
        """), {"k": f"{SCHEMA_VERSION}|{key}", "p": payload, "t": ttl})

        with _writes_lock:
            _writes += 1
            sweep = _writes % _SWEEP_EVERY == 0
        if sweep:
            db.execute(text("DELETE FROM search_cache_entries WHERE expires_at < now()"))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        log.debug("shared search cache write failed", exc_info=True)
