"""One heavy rewrite of `stories` at a time, across ALL of them.

Every bulk pass over `stories` already says, in its own words, that two of
itself at once is a disaster. `popularity_rank.py`: "Two of them at once do
not take turns — they contend for the same rows against a crawler that is also
updating them, and the whole database goes with it." `crossover.py` and
`content_gates.py`: "two passes rewriting the same rows in different orders is
the textbook deadlock, and it has cost this box an outage once already."

The argument is right and it was only ever enforced PER JOB. Each took its own
advisory key, so `popularity_rank` excluded a second `popularity_rank` and
nothing else — while the thing it actually contends with is whichever OTHER
pass happens to be walking the same 20M rows.

**Measured 2026-09-29, on the live box.** Two of them rewriting `stories` at
once, from `pg_stat_activity`:

    576412  active  00:05:16  UPDATE stories s  SET popularity   = pf.popularity ...
    585434  active  00:00:51  UPDATE stories st SET is_crossover = want.v ...

with Postgres at 73% CPU, load average 6.9, and cold searches measured at
5-10s against the 1.5-3.5s the same queries cost on a quiet box. The same
collision is what took the Harry Potter hub down that morning: crossover's
`DROP TRIGGER ... ON stories` queued behind the popularity pass for 5m15s, and
a queued ACCESS EXCLUSIVE blocks every reader behind it.

It is not bad luck and it does not need a coincidence. `_popularity_loop`
starts 15 minutes after the worker boots and runs for ~3h51m; `_curation_loop`
starts 45 minutes after boot, i.e. **45 minutes into** a four-hour pass, and
both are weekly. They collide by construction, every week, at the same offset.

Two things this fixes that a per-job key could not:

1. **Cross-job exclusion**, which is the whole point.
2. **Self-exclusion that actually holds.** Three of the four take their key on
   a `db_session()`, which hands its connection back to the pool on commit —
   and those passes commit per batch, by design, because the batch is the unit
   of progress. So their "ONE AT A TIME, ENFORCED" was released at the first
   batch boundary. Only `popularity_rank`, which is pinned for its temp
   tables, ever really held one. This lock lives on its OWN connection, opened
   for the pass and held until it ends, so no amount of committing inside the
   pass can drop it.

`try`, never `wait`. A deferred pass has nothing to add by queueing: it raises
`MaintenanceDeferred`, the caller's loop retries in an hour, and a weekly job
an hour late is a weekly job. Waiting would instead hold a pooled connection
idle for up to four hours, and the connection ceiling on this box is already
the thing `api/stats.py` records an outage shape for.

Dry runs do NOT take it. They write nothing, `tests/test_maintenance_timeouts.py`
calls all of them, and a test suite that can be blocked by whatever the live
worker happens to be doing is a test suite that fails for reasons nobody can
reproduce.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text

from db.session import engine

log = logging.getLogger("maintenance_lock")

# Any stable 64-bit number, distinct from every per-job key in this repo
# (popularity 8_531_197_402_664_219, crossover 0x0C205501,
#  content_gates 0x6721C0DE, series_wordcount 0x5E21E5CD).
# This one spells HEAVY.
HEAVY_KEY = 0x48454156_59

# How long a loop should wait before trying again after being deferred. An
# hour, because the passes this guards are weekly or six-hourly: the cost of
# being an hour late is nil, and the cost of retrying too eagerly is a pass
# that spends the whole of somebody else's four-hour run asking.
RETRY_SECONDS = int(os.getenv("MAINTENANCE_RETRY_SEC", "3600"))


class MaintenanceDeferred(Exception):
    """Another bulk pass over `stories` holds the lock. Try later, do not queue."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name}: another heavy maintenance pass is running")
        self.name = name


@contextmanager
def heavy_pass(name: str) -> Iterator[None]:
    """Hold the one heavy-maintenance slot for the duration of the block.

    Raises `MaintenanceDeferred` immediately if another pass has it.
    """
    conn = engine.connect()
    held = False
    try:
        held = bool(conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": HEAVY_KEY}).scalar())
        if not held:
            log.warning("%s: deferred, another heavy pass holds the lock", name)
            raise MaintenanceDeferred(name)
        log.info("%s: holding the heavy-maintenance lock", name)
        yield
    finally:
        # The connection goes back to the POOL rather than to the server, and a
        # session advisory lock outlives that — so it has to be given up by
        # name. A process that dies instead drops the socket, and Postgres
        # releases it for us; that is the whole reason this is an advisory lock
        # and not a flag in a table.
        if held:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": HEAVY_KEY})
                conn.commit()
            except Exception as e:  # pragma: no cover - release must not mask the pass
                log.warning("%s: could not release the heavy lock: %s", name, e)
        conn.close()
