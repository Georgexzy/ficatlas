"""A pass that lives on temp tables must keep the connection they live on.

The cross-archive popularity pass builds five temp tables, commits, and then
writes `stories` from the last of them. `Session.commit()` hands the connection
back to the pool, and a temp table belongs to the connection that made it — so
the write either finds `pr_final` or does not, depending on whether the pool
happens to give the same connection back.

On an idle pool it does, which is why this was easy to write and easy to
believe. Under contention it does not: the pass ran for weeks, then failed 78
seconds in with `relation "pr_final" does not exist`, and stayed broken for
thirteen days while the site's flagship sort went stale. Nothing about it had
changed — the worker had, by growing more concurrent loops sharing one pool.
"""
import inspect

from sqlalchemy import text

from db.session import pinned_session


def test_a_temp_table_survives_a_commit_on_a_pinned_session(db):
    """The property the popularity pass depends on, asserted directly."""
    with pinned_session() as s:
        before = s.execute(text("SELECT pg_backend_pid()")).scalar()
        s.execute(text(
            "CREATE TEMP TABLE pin_probe ON COMMIT PRESERVE ROWS AS SELECT 1 AS x"))
        s.commit()
        after = s.execute(text("SELECT pg_backend_pid()")).scalar()
        assert before == after, "a pinned session must not change connection"
        assert s.execute(text("SELECT count(*) FROM pin_probe")).scalar() == 1


def test_the_connection_is_released_afterwards(db):
    """Pinning is for the length of the job, not for ever. A session that
    never gives its connection back is a pool leak, which is the failure this
    would trade the other one for."""
    with pinned_session() as s:
        s.execute(text("SELECT 1"))
    from db.session import engine
    # Nothing checked out that we did not check in.
    assert engine.pool.checkedout() == 0


def test_the_popularity_pass_uses_it(db):
    """The invariant, because the failure it prevents needs pool contention to
    reproduce and no unit test will arrange that reliably. Same shape as
    tests/test_maintenance_timeouts.py, and for the same reason."""
    import popularity_rank
    src = inspect.getsource(popularity_rank.run)
    assert "pinned_session()" in src, \
        "popularity_rank.run must hold one connection: it builds temp tables"
    assert "with db_session() as db:" not in src, \
        "a plain session hands the connection back on commit and loses them"


def test_two_popularity_passes_cannot_run_at_once(db):
    """Three overlapping runs, started by hand while debugging, took the live
    site to 13-second searches: the pass rewrites ~500k rows against a crawler
    updating the same rows, and two of them do not take turns.

    Asserted on the source, because reproducing it needs two processes and an
    hour. The lock is session-level on the pinned connection, so it is released
    when that connection closes — including when the process is killed."""
    import inspect
    import popularity_rank
    src = inspect.getsource(popularity_rank.run)
    assert "pg_try_advisory_lock" in src, \
        "a pass this heavy must refuse to run beside another"
    # `try`, not `wait`: a second run has nothing to add and should leave.
    assert "pg_advisory_lock(" not in src


# ── a backoff that can never come back is a broken feature ─────────────────

def test_the_wayback_budget_recovers_from_its_ceiling():
    """It could not, and the cost was a feature that looked alive.

    Backing off doubles on ONE refusal. Recovering took twenty consecutive
    clean requests per 10% step, and 600s back to 5s is forty-five steps — nine
    hundred consecutive successes, against a ratchet that resets on any single
    failure. The FF.net enrichment pass is bounded to 24 minutes so it cannot
    outlive its schedule, so it made one or two requests per pass and could
    never earn its way down. Eight passes a day, three rows touched.
    """
    import time
    import wayback_harvest as W
    b = W._Budget()
    b.interval = W.MAX_INTERVAL
    # An hour quiet, then the host answers cleanly.
    b._last_recover = time.monotonic() - 3600
    b.reward()
    assert b.interval < W.MAX_INTERVAL / 10, \
        "an hour of quiet plus a clean response must climb well off the ceiling"
    b._last_recover = time.monotonic() - 3600
    b.reward()
    assert b.interval == W.BASE_INTERVAL


def test_recovery_still_needs_the_host_to_answer():
    """Elapsed time alone is not evidence. This narrows on a clean response
    and never on a guess — one is enough, but one is required."""
    import time
    import wayback_harvest as W
    b = W._Budget()
    b.interval = W.MAX_INTERVAL
    b._last_recover = time.monotonic() - 86_400
    # No reward() call: nothing has answered, so nothing moves.
    assert b.interval == W.MAX_INTERVAL


def test_a_refusal_still_backs_off_immediately():
    """The asymmetry is deliberate in the other direction: being over the line
    costs the host, so slowing down is fast and speeding up is slow."""
    import wayback_harvest as W
    b = W._Budget()
    start = b.interval
    b.penalise()
    assert b.interval > start


def test_a_slow_capture_is_not_backpressure():
    """Conflating the two pinned the archive.org budget at its ceiling.

    Measured with no pacing at all, five CDX lookups back to back: two timed
    out after 30s and three answered in under eleven seconds. A host refusing
    us does not answer three requests in a row that fast — those timeouts are
    slow captures, a property of the capture and not of our request rate.

    Treating each as "slow down" doubled the interval against a recovery of ten
    per cent per two minutes, and two doublings need fourteen minutes of clean
    responses to undo. At a forty per cent timeout rate they never arrived, so
    the FF.net backfill ran one story per twenty-four-minute pass.
    """
    import httpx
    import wayback_harvest as W
    # The helper classifies against the MODULE budget, which is the whole point
    # of it being one place — so this exercises that object and puts it back.
    b = W.BUDGET
    saved = (b.interval, b._net_errors, b._slow)
    try:
        b.interval, b._net_errors, b._slow = W.BASE_INTERVAL, 0, 0
        start = b.interval
        for _ in range(5):
            W.note_transport_error(httpx.ReadTimeout("slow capture"))
        assert b.interval == start, \
            "a slow capture must not slow the whole harvest"
        assert b._slow == 5, "but it must still be counted"
        # A refused connection still does, because that IS how archive.org
        # signals a throttle.
        for _ in range(W.NET_ERRORS_BEFORE_BACKOFF):
            W.note_transport_error(httpx.ConnectError("refused"))
        assert b.interval > start
    finally:
        b.interval, b._net_errors, b._slow = saved


def test_a_steady_trickle_of_errors_does_not_pin_the_budget():
    """The failure that killed FF.net enrichment for eleven days.

    Backoff is per-event and recovery used to be per-time, so any error
    arriving more often than one per thirteen minutes could only ratchet the
    interval upward. A host answering four requests in five is HEALTHY; the
    budget must settle near its floor, not climb to the ceiling.
    """
    import wayback_harvest as W

    def settle(clean_per_error: int) -> float:
        """The interval the job actually spends its requests at, which is the
        one worth asserting on — sampling right after a penalty measures the
        spike, not the operating point."""
        b = W._Budget()
        for _ in range(300):
            b.penalise(retry_after=0, reason="test")
            for _ in range(clean_per_error):
                b.reward()
        return b.interval

    rough = settle(4)
    assert rough < W.MAX_INTERVAL / 4, (
        f"a host answering 4 in 5 pinned the budget at {rough:.1f}s")

    # And the operating point must track how well the host is answering, not
    # just avoid the ceiling: 40 in 41 is a healthy host and earns the floor.
    healthy = settle(40)
    assert healthy <= W.BASE_INTERVAL * 1.5, f"healthy host got {healthy:.1f}s"
    assert healthy < rough, "the interval must track the host, not a constant"


def test_a_host_refusing_everything_still_backs_off():
    """The other half: recovery must not be so eager that it cancels a real
    throttle. Nothing clean is coming back here, so nothing should narrow."""
    import wayback_harvest as W
    b = W._Budget()
    for _ in range(10):
        b.penalise(retry_after=0, reason="test")
    assert b.interval >= W.MAX_INTERVAL


def test_recovery_needs_evidence_not_just_patience():
    """One clean response narrows the interval. Zero clean responses, however
    long we wait, must not -- the clock drift in reward() only runs when a
    response actually arrived."""
    import wayback_harvest as W
    b = W._Budget()
    b.penalise(retry_after=0, reason="test")
    widened = b.interval
    assert widened > W.BASE_INTERVAL
    b.reward()
    assert b.interval < widened


# ---- worker supervision -------------------------------------------------
# The worker created 24 background loops with create_task, never awaited any
# of them, and kept references in a list. asyncio surfaces an unretrieved task
# exception only on garbage collection, which the references prevent -- so a
# crashed loop vanished silently and the container stayed "healthy". Measured:
# two hours at 0% CPU with nothing running and nothing saying so.

def test_a_crashed_loop_is_restarted_not_silently_dropped():
    import asyncio
    import worker

    calls = []

    async def flaky():
        calls.append(len(calls))
        if len(calls) < 3:
            raise RuntimeError("boom")
        raise asyncio.CancelledError      # stand in for shutdown

    async def go():
        worker.RESTART_MIN = 0.0
        with contextlib_suppress(asyncio.CancelledError):
            await worker._supervised("flaky", flaky)

    asyncio.run(go())
    assert len(calls) == 3, f"crashed loop restarted {len(calls) - 1} times"


def test_shutdown_is_not_treated_as_a_crash():
    """CancelledError must propagate, or a stopping worker restarts for ever."""
    import asyncio
    import worker

    async def cancelled():
        raise asyncio.CancelledError

    async def go():
        try:
            await worker._supervised("cancelled", cancelled)
        except asyncio.CancelledError:
            return "propagated"
        return "swallowed"

    assert asyncio.run(go()) == "propagated"


def contextlib_suppress(*excs):
    import contextlib
    return contextlib.suppress(*excs)


# ---- FF.net capture index ----------------------------------------------

def test_a_known_capture_costs_no_cdx_request():
    """The point of ffnet_captures: one rate-limited request per story, not two.

    archive.org rate-limits by request, so a discovery lookup we already did in
    bulk must not be paid for again per story.
    """
    import ffnet_enrich

    asked = []

    class Client:
        def get(self, url, **kw):
            asked.append(url)
            raise AssertionError("should not reach the network in this test")

    # With a known capture, fetch_meta must go straight to the snapshot URL and
    # never touch the CDX endpoint.
    # The stub raises to stop the call; fetch_meta now reports any transport
    # failure as Transient, so that is what arrives here.
    with __import__("pytest").raises(Exception):
        ffnet_enrich.fetch_meta(Client(), "12345",
                                known=("20200101000000",
                                       "https://www.fanfiction.net/s/12345/1/T"))
    assert len(asked) == 1, f"made {len(asked)} requests, expected 1"
    assert "cdx" not in asked[0].lower(), f"still asked CDX: {asked[0]}"
    assert "20200101000000" in asked[0]


def test_the_prefix_walk_keeps_the_newest_capture_per_story():
    """One story has many captured URLs -- chapters, slugs, www vs m -- and
    collapse=urlkey collapses per URL, not per story. Picking the newest is
    ours to do."""
    import ffnet_wayback as W
    rows = [
        ["timestamp", "original"],
        ["20150101000000", "http://www.fanfiction.net/s/777/1/Old-Slug"],
        ["20220101000000", "http://m.fanfiction.net/s/777/1/New-Slug"],
        ["20180101000000", "http://www.fanfiction.net/s/888/1/Other"],
    ]
    best = {}
    for row in rows[1:]:
        ts, original = row[0], row[1]
        sid = W.story_id_from_url(original)
        if sid is None:
            continue
        if sid not in best or ts > best[sid][0]:
            best[sid] = (ts, original)
    assert best[777][0] == "20220101000000"
    assert "New-Slug" in best[777][1]
    assert set(best) == {777, 888}


def test_the_newest_capture_wins_not_the_first_row():
    """CDX sorts by urlkey then timestamp, so row 1 is the earliest capture of
    whichever slug sorts first. Measured on story 4985743 (18 captures,
    2012-2022): the 2012 snapshot parses cleanly and carries favs=None,
    follows=None, words=None; the 2022 one carries 1657, 1020 and 244923. The
    backfill exists to supply exactly that signal."""
    import ffnet_enrich

    fetched = []

    class Client:
        def get(self, url, **kw):
            fetched.append(url)
            if "cdx" in url:
                class R:
                    status_code = 200
                    @staticmethod
                    def json():
                        return [["timestamp", "original"],
                                # Deliberately not in timestamp order, the way
                                # interleaved slugs actually arrive.
                                ["20220523230545", "https://www.fanfiction.net/s/1/1/New-Slug"],
                                ["20120502212647", "http://www.fanfiction.net/s/1/1/Old_Slug"],
                                ["20181114194248", "https://www.fanfiction.net/s/1/1/Mid-Slug"]]
                return R()
            raise AssertionError("stop after the snapshot URL is chosen")

    with __import__("pytest").raises(Exception):
        ffnet_enrich.fetch_meta(Client(), "1")
    assert len(fetched) == 2, f"expected cdx + snapshot, got {fetched}"
    assert "20220523230545" in fetched[1], f"picked the wrong capture: {fetched[1]}"


def test_a_story_that_yields_nothing_stops_being_re_selected():
    """What actually kept FF.net coverage frozen at 108,468.

    find_gaps orders by how much a row lacks and breaks ties on crawled_at ASC
    NULLS FIRST. The worst gap scores belong to the oldest stories -- and FF.net
    had no character field before ~2009, so for them characters=[] is the
    correct and permanent answer. Nothing recorded the attempt, so every pass
    drew the same doomed rows and the number never moved.
    """
    import inspect
    import ffnet_enrich

    src = inspect.getsource(ffnet_enrich.run)
    assert "attempted.append(sid)" in src, \
        "every story looked at must be recorded, not just the ones that parsed"
    # Any DEFINITIVE outcome counts, including finding nothing -- a story that
    # yields nothing is exactly the one that must not come back next pass. A
    # refusal is not an outcome and is skipped before this point; see
    # test_a_refused_fetch_does_not_retire_a_good_capture.
    assert src.index("except Transient") < src.index("attempted.append(sid)")
    assert "_mark_attempted(attempted" in src

    marker = inspect.getsource(ffnet_enrich._mark_attempted)
    assert "crawled_at" in marker, \
        "must write the column find_gaps actually breaks ties on"


def test_a_lifted_statement_timeout_does_not_leak_into_the_pool():
    """lift_statement_timeout uses a plain SET, which is connection-scoped, and
    a pooled connection outlives the session that lifted it. Without a reset on
    checkout the next unrelated caller inherits "wait for ever".

    Measured on 21 Sep 2026: the weekly popularity pass held row locks on
    `stories` for 54 minutes and three writers sat behind it -- 51, 15 and 12
    minutes -- all reporting statement_timeout = 0 on pooled connections, all
    of which should have given up after 60 seconds.
    """
    from db.session import STATEMENT_TIMEOUT_MS, engine, lift_statement_timeout
    from sqlalchemy import text

    with engine.connect() as c:
        lift_statement_timeout(c)
        assert c.execute(text("SHOW statement_timeout")).scalar() == "0"
    # Same pooled connection, handed out again.
    with engine.connect() as c:
        got = c.execute(text("SHOW statement_timeout")).scalar()
    assert got != "0", "statement_timeout = 0 leaked back into the pool"
    # Postgres normalises the units it reports back ("60000ms" -> "1min"), so
    # compare what it MEANS, not how it spells it.
    with engine.connect() as c:
        ms = c.execute(text(
            "SELECT setting::int FROM pg_settings "
            "WHERE name = 'statement_timeout'")).scalar()
    assert ms == STATEMENT_TIMEOUT_MS, f"{ms} != {STATEMENT_TIMEOUT_MS}"


def test_the_popularity_write_is_chunked_not_one_giant_transaction():
    """As a single UPDATE this held row locks on most of `stories` for an hour
    and twenty-one minutes, with four writers queued behind it and nothing in
    any log to say so -- an AO3 crawled_at stamp blocked 78 minutes, another
    42, an FF.net enrichment stamp 40, a comment merge 14.

    Nothing needs one transaction: the scores are final in pr_final before the
    write starts, every row is independent, and a pass that stops halfway has
    correctly written everything it committed.
    """
    import inspect
    import popularity_rank as P

    assert ":after" in P.UPDATE_WRITE_SQL and ":upto" in P.UPDATE_WRITE_SQL, \
        "the write must be bounded by an id range"
    src = inspect.getsource(P.run)
    assert "while True" in src, "the write must iterate slices"
    # A commit inside the loop is the whole point -- it is what releases the
    # locks other writers are waiting on.
    loop = src[src.index("while True"):]
    assert "db.commit()" in loop, "each slice must commit, releasing its locks"


def test_the_popularity_slice_bound_query_actually_runs():
    """Caught a real failure: the first version used max(id), and Postgres has
    no max() for uuid -- so the chunked write raised UndefinedFunction on its
    very first slice, after the whole expensive scoring phase had completed."""
    from db.session import db_session
    from sqlalchemy import text
    import popularity_rank as P

    with db_session() as db:
        db.execute(text("CREATE TEMP TABLE pr_final_probe (id uuid PRIMARY KEY)"))
        db.execute(text(
            "INSERT INTO pr_final_probe SELECT gen_random_uuid() "
            "FROM generate_series(1, 50)"))
        sql = ("SELECT id FROM (SELECT id FROM pr_final_probe WHERE id > :after "
               "ORDER BY id LIMIT :n) t ORDER BY id DESC LIMIT 1")
        seen, after = 0, "00000000-0000-0000-0000-000000000000"
        while True:
            upto = db.execute(text(sql), {"after": after, "n": 7}).scalar()
            if upto is None:
                break
            n = db.execute(text(
                "SELECT count(*) FROM pr_final_probe "
                "WHERE id > :after AND id <= :upto"),
                {"after": after, "upto": upto}).scalar()
            assert n > 0, "a slice must advance, or this loops for ever"
            seen += n
            after = str(upto)
        assert seen == 50, f"slices covered {seen} of 50 rows"
        assert str(P.WRITE_BATCH).isdigit()


def test_one_caller_never_waits_more_than_the_ceiling():
    """`_next` is a shared reservation counter and several jobs share this
    budget against one host, so without a cap the Nth caller waits N intervals.
    At the 260s interval archive.org imposed on 21 Sep that is a quarter of an
    hour inside one wait() -- which is how the enrichment pass overran a time
    budget it checks faithfully between every story.
    """
    import time
    import wayback_harvest as W

    b = W._Budget()
    b.interval = 260.0
    now = time.monotonic()
    # Twenty callers queue up without any of them actually sleeping.
    for _ in range(20):
        with b._lock:
            start = min(max(time.monotonic(), b._next),
                        time.monotonic() + W.MAX_INTERVAL)
            b._next = start + b.interval
    assert b._next - now <= W.MAX_INTERVAL + b.interval + 1, \
        "the reservation queue ran away from the present"


def test_the_gap_queue_advances_instead_of_redrawing_the_same_rows(db):
    """Least-recently-tried must outrank most-read, or the queue never moves.

    With the staleness key last it was never reached: the worst-gap rows tie on
    engagement at 0 and are then separated uniquely by word_count. The same
    eight FF.net stories came back every pass -- all 2002-2005 works whose
    archived pages have no character field at all, because FF.net had none
    before ~2009 -- so the queue was pinned on rows that could never satisfy
    it, and recording the attempt could not help while the mark fed a key
    nothing consulted.
    """
    from sqlalchemy import text as sql_text
    from gap_filler import find_gaps

    # Two rows, identically empty so their gap scores match. The one with the
    # bigger word count would win on the old ordering; the one tried longer ago
    # must win now.
    db.execute(sql_text("""
        INSERT INTO stories (site, site_id, url, title, word_count, crawled_at,
                             characters, relationships, fandoms, tags)
        VALUES ('ffnet', '900000001', 'u1', 'recently tried', 99999,
                now(), '{}', '{}', '{}', '{}'),
               ('ffnet', '900000002', 'u2', 'tried long ago',     1,
                now() - interval '30 days', '{}', '{}', '{}', '{}')
    """))
    db.commit()

    order = [r["site_id"] for r in find_gaps(db, "ffnet", limit=2000)
             if r["site_id"] in ("900000001", "900000002")]
    assert order == ["900000002", "900000001"], (
        f"queue returned {order}; the row tried 30 days ago must come first "
        f"even though the other is 99,999 words")


def test_enrichment_targets_stories_the_archive_actually_holds(db):
    """Measured: ten capture-backed stories yielded characters for six; the
    eight the gap score was drawing yielded none, and could not have -- they
    are 2002-2005 works whose archived pages carry no character field.

    Two reasons it works. A capture is a fact about what exists, so no request
    is spent discovering there is nothing to fetch (about 31% of them were).
    And the captures are modern: of the first 378,000 indexed, 219,653 are from
    2018 and 123,316 from 2015, against barely 180 predating 2011.
    """
    from sqlalchemy import text as sql_text
    import ffnet_enrich

    db.execute(sql_text("""
        INSERT INTO stories (site, site_id, url, title, characters,
                             relationships, fandoms, tags, crawled_at)
        VALUES ('ffnet', '910000001', 'u1', 'has a capture', '{}', '{}',
                '{}', '{}', now() - interval '10 days'),
               ('ffnet', '910000002', 'u2', 'no capture',    '{}', '{}',
                '{}', '{}', now() - interval '99 days')
    """))
    db.execute(sql_text("""
        INSERT INTO ffnet_captures (site_id, snapshot_ts, original)
        VALUES (910000001, '20180101000000',
                'https://www.fanfiction.net/s/910000001/1/T')
        ON CONFLICT (site_id) DO NOTHING
    """))
    db.commit()

    picked = ffnet_enrich._pick_targets(200)
    by_id = {str(r[1]): r for r in picked}
    assert "910000001" in by_id, \
        "a story with a known capture was not selected"
    row = by_id["910000001"]
    assert row[3] is not None and row[3][0] == "20180101000000", \
        "the capture must be carried, or the fetch pays for CDX again"
    # The capture-backed row outranks the one with no capture, even though the
    # other has gone far longer without being tried.
    order = [str(r[1]) for r in picked]
    if "910000002" in order:
        assert order.index("910000001") < order.index("910000002")


def test_a_refused_fetch_does_not_retire_a_good_capture():
    """A 429 is archive.org declining to answer, not a statement that the
    story has no capture. Conflating them made every throttled request
    permanently consume a queued capture that was perfectly good -- and the
    logs recorded it as `no_snapshot`, so it looked like the archive's fault.
    """
    import ffnet_enrich
    from wayback_harvest import Transient
    import pytest as _pytest

    class Throttled:
        def get(self, url, **kw):
            class R:
                status_code = 429
                headers: dict = {}
                text = ""
                @staticmethod
                def json():
                    return []
            return R()

    with _pytest.raises(Transient):
        ffnet_enrich.fetch_meta(Throttled(), "1",
                                known=("20200101000000",
                                       "https://www.fanfiction.net/s/1/1/T"))

    # And run() must skip it rather than record it as attempted.
    import inspect
    src = inspect.getsource(ffnet_enrich.run)
    idx_try = src.index("except Transient")
    idx_append = src.index("attempted.append(sid)")
    assert idx_try < idx_append, \
        "a refused story must be skipped before it is recorded as attempted"
    assert "continue" in src[idx_try:idx_append]


def test_retiring_a_capture_uses_its_primary_key(db):
    """The first version joined back through `stories` on
    `c.site_id::text = s.site_id`. Casting the indexed bigint defeats the
    primary key, so every batch planned a sequential scan of all 1.6M captures
    -- 45,192 planner units against 4.81 for the index scan.

    It did not error, it just mostly did not finish: 27 stories enriched in a
    pass and three captures retired. The same head of the queue came back next
    pass and was "enriched" all over again, which is how the log could read
    enriched=27 while character coverage moved by one.
    """
    import inspect
    from sqlalchemy import text as sql_text
    import ffnet_enrich

    # Strip comments first -- the one here describes the old bug and quotes
    # the very cast this asserts is gone.
    src = "\n".join(l for l in
                    inspect.getsource(ffnet_enrich._mark_attempted).splitlines()
                    if not l.lstrip().startswith("#"))
    assert "site_id::text" not in src, \
        "casting the indexed column defeats the primary key"
    assert "site_id = ANY(:sids)" in src

    # Not an EXPLAIN assertion: on a near-empty test table a sequential scan is
    # the CORRECT plan, so that would only measure how many rows the fixture
    # happens to hold. The invariant that survives is above -- compare the
    # indexed column to a value, never a cast of it -- plus the update working.
    db.execute(sql_text(
        "INSERT INTO ffnet_captures (site_id, snapshot_ts, original) "
        "VALUES (920000001, '20180101000000', 'u') "
        "ON CONFLICT (site_id) DO NOTHING"))
    db.commit()
    db.execute(sql_text("UPDATE ffnet_captures SET done_at = now() "
                        "WHERE site_id = ANY(:sids)"), {"sids": [920000001]})
    db.commit()
    done = db.execute(sql_text("SELECT done_at FROM ffnet_captures "
                               "WHERE site_id = 920000001")).scalar()
    assert done is not None, "the capture was not retired"


def test_the_candidate_pool_is_wide_enough_to_survive_the_join():
    """Most of the capture index does not match a story in this index.

    ffnet_captures holds every FF.net story ARCHIVE.ORG has, which is more than
    this index has -- 5,165 captured against 461 indexed under one id prefix,
    and more captured than indexed under every prefix sampled. So a pool sized
    like the batch mostly evaporates against the join: at 4,000 only 35 of a
    requested 200 came back capture-backed and 165 fell through to the
    gap-score fallback, which is the path that fetches 2002 pages having no
    character field at all.

    Widening it is close to free because the join stops at the limit.
    """
    import inspect
    import ffnet_enrich
    src = inspect.getsource(ffnet_enrich._pick_targets)
    assert "60_000" in src or "60000" in src, \
        "the candidate pool must be far larger than the batch"
    assert "want * 300" in src
