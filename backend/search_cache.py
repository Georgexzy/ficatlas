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

Per process, so with four workers there are four caches and the hit rate is
correspondingly lower than a shared one. That is the same trade the rate limiter
makes: a shared Redis would be exact and is not worth a second service.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional

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
