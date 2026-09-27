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


# ---- extractor: containment beats frequency ------------------------------
#
# A unit test on the rule itself. The endpoint needs the whole 20M-work facet
# vocabulary to say anything at all, which the test database does not have --
# so exercising it end to end here would assert on an empty string and pass for
# the wrong reason.

def _cand(kind, value, count, span, rank=0):
    return {"kind": kind, "value": value, "count": count, "span": span,
            "rank": rank, "n": span[1] - span[0]}


def test_a_multi_word_fandom_beats_a_common_word_inside_it():
    """"[Highschool DXD] Fanfics" came out as tag:"Highschool AU".

    The candidate sort is by count, so the tag `highschool` (1,057 works)
    claimed its span before the FANDOM `Highschool DxD` (86) -- an exact
    two-word match and the entire subject of the post -- was ever considered.
    """
    from api.search import _drop_contained
    kept = _drop_contained([
        _cand("tag", "highschool", 1057, (0, 1)),
        _cand("fandom", "Highschool DxD", 86, (0, 2)),
    ])
    assert [c["value"] for c in kept] == ["Highschool DxD"]


def test_containment_does_not_undo_what_the_count_sort_was_for():
    """Both failures the count-first sort exists to prevent are DISJOINT spans,
    where nothing sits inside anything, so containment never fires on them."""
    from api.search import _drop_contained
    kept = _drop_contained([
        _cand("tag", "Fluff", 1130841, (1, 2)),
        _cand("tag", "one shots", 1561, (2, 4)),
    ])
    assert {c["value"] for c in kept} == {"Fluff", "one shots"}, \
        "disjoint terms must both survive; count decides between them"


def test_equal_spans_are_both_kept_for_the_sort_to_choose_between():
    """A fandom and a tag can be spelled alike. Picking one is the sort's job."""
    from api.search import _drop_contained
    kept = _drop_contained([
        _cand("fandom", "Marvel", 686826, (0, 1)),
        _cand("tag", "Marvel", 46645, (0, 1)),
    ])
    assert len(kept) == 2


def test_the_longer_name_still_wins_the_way_the_docstring_promised():
    from api.search import _drop_contained
    kept = _drop_contained([
        _cand("char", "Daphne", 79, (0, 1)),
        _cand("char", "Greengrass", 60, (1, 2)),
        _cand("ship", "Daphne Greengrass/Harry Potter", 1035, (0, 2)),
    ])
    assert [c["value"] for c in kept] == ["Daphne Greengrass/Harry Potter"]


# ---- search: typo tolerance for titles with a common opener --------------

def test_a_title_starting_with_a_common_opener_still_gets_typo_tolerance():
    """"all the yung dudes" returned four works, none of them All the Young
    Dudes -- the most-kudosed work in this index at 322,055. One letter.

    The multi-word fuzzy path bounds itself with a range scan on the first two
    words, which cannot serve "all the%" (tens of thousands of titles), so
    those queries were skipped entirely and had no typo tolerance at all.
    """
    import inspect
    from api import search as S
    src = inspect.getsource(S.search)
    assert "common_opener" in src
    # The trigram operator is the fallback, and it must match the INDEXED
    # expression: ix_stories_title_trgm is gin (title gin_trgm_ops), so a
    # lower(title) predicate cannot use it.
    assert 'Story.title.op("%")(q_norm)' in src
    assert 'func.lower(Story.title).op("%")' not in src


def test_the_trigram_module_is_loaded_before_its_setting_is_changed():
    """pg_trgm's GUC does not exist until the module is loaded into the
    session, so a bare SET LOCAL raises "unrecognized configuration parameter"
    -- which is how this branch first came to fire and contribute nothing at
    all, silently, while looking correct."""
    import inspect
    from api import search as S
    src = inspect.getsource(S.search)
    assert "show_limit()" in src
    assert src.index("show_limit()") < src.index("pg_trgm.similarity_threshold")


def test_the_fuzzy_arm_is_ordered_before_it_is_cut():
    """An unordered LIMIT is an arbitrary cut. Fifty matching titles were kept
    in scan order, so the All the Young Dudes everybody means was not among
    them and the reader saw three namesakes with 544, 0 and 84 kudos."""
    import inspect
    from api import search as S
    src = inspect.getsource(S.search)
    arm = src[src.index("parts.append(\n                fuzzy_q"):]
    assert "order_by" in arm[:400], "the fuzzy arm must be ordered before limit"
    assert "similarity" in arm[:400] and "kudos" in arm[:400]


# ---- describe: the reader's own words, ranking rather than filtering -----

def test_the_description_ranks_and_never_filters():
    """`q` is matched with websearch_to_tsquery, which ANDs -- every word a
    requirement. That is exactly why a post's prose could never go in there: a
    two-hundred-word request minus its framing is a hundred-and-eighty
    requirements and matches nothing. As a RANK the same words cost nothing
    when absent and lift a work for each one present."""
    import inspect
    from api import search as S
    src = inspect.getsource(S._describe_rank)
    assert '" or ".join(words)' in src, "the description must be OR'd, not ANDed"
    assert "ts_rank" in src
    # And it must not appear in any filter predicate.
    full = inspect.getsource(S.search)
    assert "describe" in full
    assert "filter(describe" not in full


def test_the_vocabulary_of_asking_is_not_the_vocabulary_of_the_fic():
    """"looking", "remember", "fic", "story" appear in every fic-finder post
    and in no summary worth ranking, so they discriminate between nothing."""
    from api.search import _describe_words
    got = _describe_words(
        "Looking for a fic I read years ago, I think the story had "
        "suppressants and an omegaverse hospital")
    assert "looking" not in got and "story" not in got and "read" not in got
    assert "suppressants" in got and "omegaverse" in got


def test_a_description_with_nothing_usable_ranks_by_nothing():
    """Below two usable words there is no signal, and ordering by noise is
    worse than ordering by readership."""
    from api.search import _describe_rank
    from models.story import Story
    assert _describe_rank(Story, "") is None
    assert _describe_rank(Story, "looking for a fic please") is None


def test_the_description_is_capped():
    """ts_rank over a hundred OR'd lexemes costs real time, and the tail of a
    long post is reminiscence rather than description."""
    from api.search import _describe_words
    long_post = " ".join(f"word{i}" for i in range(200))
    assert len(_describe_words(long_post)) <= 24


# ---- negation scoping ----------------------------------------------------

def test_a_refusal_stops_at_its_own_clause(db):
    """A negation governs its clause, not the rest of the sentence.

    Without a boundary the subject matcher read straight through the next
    refusal and ATE ITS MARKER, which inverts what the reader asked for:

        "not a coffee shop au, definitely no mpreg"
            -> excluded the coffee shop, handed back "definitely mpreg" with
               the `no` gone, and Mpreg came back as a WANT.
        "no smut and no mpreg please"
            -> excluded Mpreg and left "smut" behind as a want.

    In both the reader refused something and the search went looking for it.
    """
    from sqlalchemy import text as sql_text
    from query_intent import _extract_negations

    # The vocabulary these refusals resolve against. The test database has no
    # facets of its own, and _extract_negations deliberately refuses to guess
    # at a subject it cannot resolve — so without this it correctly returns
    # nothing and the test would pass for the wrong reason.
    for tag, n in (("Smut", 500000), ("Mpreg", 90000),
                   ("Coffee Shop", 4000), ("Character Death", 300000)):
        db.execute(sql_text(
            "INSERT INTO facets (kind, value, count, norm) "
            "VALUES ('tag', :v, :c, lower(replace(:v,' ',''))) "
            "ON CONFLICT (kind, value) DO UPDATE SET count = EXCLUDED.count"),
            {"v": tag, "c": n})
    db.commit()

    rest, groups = _extract_negations(
        db, "Drarry fic, not a coffee shop au, definitely no mpreg", gated=False)
    excluded = " ".join(g[0] for g in groups).lower()
    assert "coffee shop" in excluded
    assert "mpreg" in excluded, "the second refusal's marker was eaten"
    assert "mpreg" not in rest.lower(), "a refused thing became a want"

    rest, groups = _extract_negations(
        db, "drarry fic with no smut and no mpreg please", gated=False)
    excluded = " ".join(g[0] for g in groups).lower()
    assert "smut" in excluded and "mpreg" in excluded
    assert "smut" not in rest.lower()


def test_the_clause_boundary_does_not_break_a_trailing_want(db):
    """"no character death fluff" must still take Character Death and leave
    fluff behind -- the case the negation logic was written for."""
    from sqlalchemy import text as sql_text
    from query_intent import _extract_negations
    db.execute(sql_text(
        "INSERT INTO facets (kind, value, count, norm) "
        "VALUES ('tag', 'Character Death', 300000, 'characterdeath') "
        "ON CONFLICT (kind, value) DO UPDATE SET count = EXCLUDED.count"))
    db.commit()
    rest, groups = _extract_negations(db, "no character death fluff please",
                                      gated=False)
    assert any("character death" in g[0].lower() for g in groups)
    assert "fluff" in rest.lower()


def test_a_clause_boundary_hands_the_rest_back_untouched():
    """The next refusal's marker has to survive, or the loop cannot find it."""
    from query_intent import _neg_clause
    subject, tail = _neg_clause("a coffee shop au, definitely no mpreg")
    assert subject == "a coffee shop au"
    assert "no mpreg" in tail, tail
    # No boundary at all: the whole phrase is the subject.
    assert _neg_clause("mpreg") == ("mpreg", "")


# ---- anchors: a linked fic is evidence, not a guess ----------------------

def test_a_linked_work_is_read_before_the_urls_are_stripped():
    """URLs are removed before the words are read, because a bare domain in
    prose otherwise resolves as a fandom. That threw away the strongest thing
    a fic-finder post can carry: a link to a work whose fandom and pairing this
    index already holds as recorded fact."""
    import inspect
    from api import search as S
    src = inspect.getsource(S.extract)
    assert src.index("_resolve_anchors") < src.index('re.sub(r"https?://'), \
        "anchors must be read before the URL strip destroys them"


def test_a_bare_domain_is_not_a_fandom():
    """"I stumbled upon some fanfiction ... references destinysgateway.com"
    came out as fandom:"Destiny (Video Games)" -- a fandom read off a hostname,
    on a post about Hellsing."""
    import inspect
    from api import search as S
    src = inspect.getsource(S.extract)
    assert "co\\\\.uk|me|tv" in src or "(?:com|net|org" in src, \
        "bare domains must be stripped, not just http:// ones"


def test_an_anchor_contributes_the_fandom_and_pairing_only():
    """A work carries dozens of tags, most incidental, and every one added to a
    query is another requirement the answer has to satisfy. The fandom and the
    pairing are what "something like this one" actually means."""
    import inspect
    from api import search as S
    src = inspect.getsource(S._resolve_anchors)
    assert "fandoms, relationships" in src
    assert "tags" not in src.split("SELECT")[1].split("FROM")[0]


# ---- AO3 request budget: the login redirect -----------------------------

def test_a_restricted_work_costs_one_request_not_two():
    """AO3 answers 302 to /users/login for works only registered users may
    read. Following that redirect spends a second request to fetch a login page
    that can never contain a work.

    Measured over an hour of live traffic: 483 of 1,548 AO3 requests were that
    login page -- 31% of the allowance that is the binding constraint on how
    fast AO3 summaries fill at all.
    """
    import inspect
    from ao3_title_repair import fetch_work
    src = inspect.getsource(fetch_work)
    assert "follow_redirects=False" in src, \
        "following the login redirect doubles the cost of every locked work"
    assert "/users/login" in src


def test_a_restricted_work_is_recorded_so_it_is_not_asked_again():
    """Nothing recorded the outcome, so the same works came round every pass
    and paid twice again -- zero rows carried source_restricted_at while the
    column was already being used to filter hubs."""
    import inspect
    import ao3_title_repair as T
    assert T.RESTRICTED is not T.THROTTLED, \
        "permanent and transient outcomes must not be the same value"
    assert "source_restricted_at" in inspect.getsource(T._flush)
    # And the candidate query must skip what was recorded, or nothing is saved.
    assert "source_restricted_at IS NULL" in T.TRUNCATED_SQL


def test_the_only_job_that_can_reach_fandomless_rows_is_scheduled():
    """~3.9M AO3 rows have no fandom at all, and ao3_listing_harvest cannot see
    them by construction -- it walks fandom tag pages and a work with no fandom
    is on none of them. ao3_stub_enrich exists for exactly those rows and was
    a manual script that nothing ever called."""
    import inspect
    import worker
    assert hasattr(worker, "_ao3_stub_loop")
    src = inspect.getsource(worker.main)
    assert "_ao3_stub_loop" in src, "the loop exists but is never started"
    assert "_supervised(\"ao3_stub_loop\"" in src, \
        "an unsupervised loop dies silently -- see _supervised"


# ---- bigram tag hints ----------------------------------------------------

def test_bigrams_are_counted_in_the_database_not_in_python():
    """The documented reason bigrams were off is exact -- they multiply the
    vocabulary ~40x, two builds were OOM-killed at 1.2GB, and the bounded
    version kept 21 of them because a rare gram is the first thing a memory cap
    discards. Every one of those is a property of counting in PYTHON. Postgres
    spills to disk and finishes."""
    import inspect
    import tag_hints
    src = inspect.getsource(tag_hints.mine_bigrams)
    assert "bg_doc" in tag_hints._BIGRAM_SQL
    assert "GROUP BY" in tag_hints._BIGRAM_HINTS_SQL
    # And it must lift the statement timeout, which is only safe because the
    # engine restores the default on pool checkout.
    assert "lift_statement_timeout" in src


def test_a_gram_is_a_name_only_when_every_word_is():
    """`names` holds 46,181 words from character and fandom vocabularies and
    necessarily contains ordinary English -- "the", "and", "home", "night",
    "time", "love" are all in it, because works are tagged with characters
    called Love and Night. Rejecting a pair for containing ANY of them rejected
    every pair: 132 candidates, 0 survivors."""
    names = {"mike", "wheeler", "peter", "parker", "time", "the", "one"}

    def is_name(gram):
        parts = gram.split()
        return bool(parts) and all(p in names for p in parts)

    assert is_name("mike wheeler")
    assert is_name("peter parker")
    assert not is_name("time loop"), "a trope phrase must survive"
    assert not is_name("one bed")


def test_a_hint_may_not_point_at_a_tag_nobody_uses():
    """Sample-relative floors cannot catch a private tag: a work carrying
    "Fraxus in a steampunk world" IS the whole population of that tag, so it
    clears any within-sample bar and arrives with an enormous lift. Both were
    learned before this guard existed."""
    import tag_hints
    assert ":min_tag_works" in tag_hints._BIGRAM_HINTS_SQL
    assert "FROM facets f" in tag_hints._BIGRAM_HINTS_SQL
    assert tag_hints.BIGRAM_MIN_TAG_WORKS >= 100


def test_the_joint_floor_is_lower_than_the_marginals():
    """Requiring the pairing to clear the same bar as each side asks for one
    specific phrase-and-tag combination twenty times in forty-five thousand
    summaries. The first run with both at 25 produced exactly one hint."""
    import tag_hints
    assert tag_hints.BIGRAM_MIN_JOINT < tag_hints.BIGRAM_MIN_DOCS


def test_the_bigram_sample_is_capped_at_what_the_box_survives():
    """Moving the counting into Postgres removed the PYTHON memory cap and that
    worked -- 0.5% mines in 200s where the Python version was OOM-killed twice.
    It did not remove the box. A 3% run drove the machine to 0GB free and was
    killed by the OOM reaper while the host was serving searches.

    So the original note was right for a reason it did not name: this needs
    more memory than this box has spare, whichever process does the counting.
    """
    import inspect
    import tag_hints
    assert tag_hints.BIGRAM_MAX_PCT <= 1.0
    src = inspect.getsource(tag_hints.mine_bigrams)
    assert "min(BIGRAM_SAMPLE_PCT, BIGRAM_MAX_PCT)" in src, \
        "a caller must not be able to discover the limit by taking the site down"


# ---- the Maze Runner post ------------------------------------------------
#
# A real post whose FIRST LINE was the archive's own name for the pairing --
# "Newt/Thomas (Maze Runner)" -- and which came out as
#     char:"Newt Scamander" char:"Thomas (Maze Runner)"
# Newt Scamander is a Fantastic Beasts character, so the two had never appeared
# together and the search returned ZERO works. Five separate faults stacked:

def test_a_relationship_leads_the_query_not_its_loose_halves():
    """`pair_chars` seeded the query with the characters either side of the
    slash, so a relationship the n-gram pass had already matched arrived third
    and was ANDed on the end."""
    import inspect
    from api import search as S
    src = inspect.getsource(S.extract)
    assert "_ship_in_terms" in src
    # Scoped to the `kept` seeding block. There is an EARLIER `elif pair_chars`
    # that reorders `terms`, which is a different stage and not what this
    # asserts about.
    seed = src[src.index("kept: list[ExtractedTerm] = []"):]
    assert seed.index("elif _ship_in_terms is not None") < seed.index("elif pair_chars")


def test_a_one_word_fandom_that_is_an_ordinary_word_is_not_evidence():
    """`After` is a real fandom, so "watching the movies after nearly a decade"
    named it. That is not merely a wrong clause: _post_fandom is what every
    character is then judged against, so one preposition read as a fandom
    DELETES the right characters for disagreeing with it."""
    from api.search import _COMMON_WORD_FANDOMS
    assert "after" in _COMMON_WORD_FANDOMS
    assert "the maze runner" not in _COMMON_WORD_FANDOMS


def test_a_characters_own_bracket_names_the_fandom():
    """The reader writes "Maze Runner"; the vocabulary has "The Maze Runner
    Series - James Dashner". No exact n-gram finds it -- but `Thomas (Maze
    Runner)` matched, and archives disambiguate characters by appending the
    fandom, so the character is naming it."""
    import inspect
    from api import search as S
    assert "_parens" in inspect.getsource(S.extract)


def test_belonging_to_a_fandom_takes_more_than_one_crossover(db):
    """EXISTS was too weak by exactly the margin that matters: crossovers
    exist, so Newt Scamander really does appear in a Maze Runner work
    somewhere, and one accident licensed him."""
    from api.search import _FANDOM_FIT_MIN, _character_fits_fandom
    assert _FANDOM_FIT_MIN > 1
    # No fandom named means no judgement to make.
    assert _character_fits_fandom(db, "Anyone", set()) is True


def test_a_term_is_judged_by_what_it_costs_not_only_what_survives():
    """tag:"The Maze Runner Spoilers" cleared the absolute floor comfortably
    and took a pairing with 3,529 works down to twelve, three of them at zero
    kudos. A weak term bought with a huge cut is a bad trade however many works
    survive it."""
    from api.search import _PROBE_MIN_RETAIN, _RATIO_CAP, _PROBE_CAP
    assert 0 < _PROBE_MIN_RETAIN < 1
    # The probe must still be counting at the top of its range, or every ratio
    # is 1.0: at a cap of 20 both 3,529 and 38 come back 20.
    assert _RATIO_CAP > _PROBE_CAP * 5


def test_a_tag_that_restates_the_pairing_adds_nothing():
    from api.search import _restates_pairing
    assert _restates_pairing("Newt/Thomas (Maze Runner)",
                             "Thomas Loves Newt (Maze Runner)")
    assert not _restates_pairing("Newt/Thomas (Maze Runner)", "Newt Whump")
    assert not _restates_pairing("Solo Character", "Anything")


# ---- aiming the cheap path -----------------------------------------------

def test_the_backfill_walks_the_gaps_not_the_biggest_fandoms():
    """It ordered by total works held, on the reasoning that every AO3 row
    arrived without a summary so "most of our works" was also "most of our
    gaps". True when written; 81% lack one now, unevenly -- so that ordering
    sent the harvest back through fandoms it had already filled. Measured: a
    Fairy Tail pass reporting "86 complete" of 160 while Harry Potter sat on
    300,647 missing summaries.

    It matters because of the exchange rate: a tag listing returns twenty works
    per request where a work page returns one.
    """
    import ao3_listing_harvest as H
    sql = str(H.BACKFILL_SQL)
    assert "fandom_gaps" in sql
    assert "g.no_summary DESC" in sql
    # NULLS LAST, not first: a fandom absent from the table is below its floor
    # of missing summaries, so it is nearly done rather than unmeasured.
    assert "NULLS LAST" in sql


def test_the_gap_recount_is_rare_and_says_why():
    """It unnests the fandom array over 14M rows -- 89 seconds measured -- and
    the answer moves far more slowly than that."""
    import inspect
    import worker
    src = inspect.getsource(worker._fandom_gaps_loop)
    assert "FANDOM_GAPS_INTERVAL_HOURS" in src
    assert "168" in src, "weekly, not per pass"


def test_backfill_keeps_the_order_the_gap_query_gave_it():
    """Re-sorting by least-walked threw the gap ordering away. The big gaps are
    all ~1,180 pages deep because they have been visited before, so Fairy Tail
    at page 485 won every time and went on yielding 25-34 enrichments per 160
    works while Harry Potter sat on 300,647 missing summaries.

    Least-walked is a fairness rule, and fairness is the wrong goal here.
    Rotation comes from the weekly gap recount: a fandom stops being first when
    its gap drops below another's."""
    import inspect
    import worker
    src = inspect.getsource(worker._listing_harvest_loop)
    # Anchored past the mode toggle at the top of the loop, which is also
    # spelled `if mode == "backfill"` and is not the branch this is about.
    anchor = 'pending = [(f, n, get_cursor(db, f, mode)) for f, n in fandoms]'
    assert anchor in src
    back = src[src.index(anchor):]
    assert "pending[0]" in back, "backfill must take the head of the gap order"
    assert "sorted(" in back, "discover must still spread by least-walked"
    # And the head must be taken for backfill, the sort reserved for discover.
    assert back.index("pending[0]") < back.index("sorted(")


# ---- the stale refresh that was dying every pass -------------------------

def test_the_stale_refresh_does_not_scan_rows_that_cannot_win():
    """Its score multiplies by ln(1 + kudos + hits), so a work with neither
    figure scores EXACTLY ZERO and can never survive the ORDER BY. 4.9M of the
    6.35M in_progress AO3 rows are in that state, so scanning them was 78% of
    the cost -- and that cost exceeded the 60s statement timeout, so the pass
    died with QueryCanceled every single time.

    In the log that is one warning among thousands. In the index it is nothing
    happening for months, which is the failure mode this whole session keeps
    turning up.
    """
    import inspect
    import worker
    src = inspect.getsource(worker._refresh_stale_loop)
    assert "COALESCE(kudos, 0) > 0 OR COALESCE(hits, 0) > 0" in src, \
        "the zero-score rows are still being scanned"
    # It must be a WHERE condition, not a change to the score itself: the
    # ordering is what makes the exclusion lossless.
    assert "ln(1 + COALESCE(kudos,0) + COALESCE(hits,0))" in src


def test_every_caller_of_fetch_work_handles_the_restricted_sentinel():
    """RESTRICTED was added to fetch_work and only one of its two callers was
    updated. It is a truthy object and not THROTTLED, so in the other it fell
    straight through to apply_work and raised "'object' object has no attribute
    'get'".

    That only surfaced after the statement-timeout fix above, because until then
    the pass died before it ever reached a fetch. One bug was hiding the next,
    which is the argument for fixing the loud one first.
    """
    import inspect
    import worker
    for fn in (worker._refresh_stale_loop, worker._title_repair_loop):
        src = inspect.getsource(fn)
        if "fetch_work(" not in src:
            continue
        assert "RESTRICTED" in src, (
            f"{fn.__name__} calls fetch_work without handling RESTRICTED")


# ---- search cache: what an expensive query earns -------------------------

def test_an_expensive_search_earns_hours_not_minutes():
    """The factor was 0.15 against a 30-minute ceiling, so a four-second search
    earned ten minutes. Cold latency tracks the result count -- 64 results in
    0.5s, 2,188 in 1.9s, popular terms at the 5,000 ceiling in 3 to 5 -- and
    those popular terms are exactly what a ten-minute window fails to cover. On
    a low-traffic site a term searched every twenty minutes was cold every
    time, so every reader paid full price."""
    from api.search import _cost_ttl, SEARCH_CACHE_SECONDS
    assert _cost_ttl(4000) >= 3600, "a four-second search must earn at least an hour"
    assert _cost_ttl(500) < _cost_ttl(4000), "cost must still decide the TTL"


def test_a_cheap_search_stays_fresh():
    """Freshness matters where a work is NEW, and a reader looking for one
    published this morning searches its title -- narrow, fast, and so barely
    cached. The floor is what protects that."""
    from api.search import _cost_ttl, SEARCH_CACHE_SECONDS
    assert _cost_ttl(10) == SEARCH_CACHE_SECONDS
    assert SEARCH_CACHE_SECONDS <= 300, "the floor is meant to be short"


def test_the_ceiling_bounds_how_stale_anything_can_get():
    from api.search import _cost_ttl, SEARCH_CACHE_MAX_SECONDS
    assert _cost_ttl(10_000_000) == SEARCH_CACHE_MAX_SECONDS
    assert SEARCH_CACHE_MAX_SECONDS <= 86400, "a day is the most defensible bound"


def test_the_warmer_warms_what_readers_search_not_what_we_do():
    """It reads visit_events_public, which excludes our own admin queries.
    Warming the Outreach panel's machine-built operator queries would spend the
    effort on searches no reader will ever repeat -- and there were 1,925 of
    those in the history."""
    import cache_warm
    assert "visit_events_public" in cache_warm.POPULAR
    assert "visit_events " not in cache_warm.POPULAR


def test_popularity_is_counted_in_people_not_searches():
    """Searches alone let one visitor -- or one paging session -- nominate the
    whole list. "Bts jin and jimin" is 474 searches from 24 people, and it is
    the people that make it popular."""
    import cache_warm
    assert "count(DISTINCT visitor) >= :min_people" in cache_warm.POPULAR
    assert cache_warm.MIN_PEOPLE >= 2


def test_the_warmer_goes_through_the_real_endpoint():
    """A warmer populating the cache by another route would warm a key the real
    request never looks under -- which makes it worse than nothing, because it
    costs the work and saves none of it."""
    import inspect
    import cache_warm
    src = inspect.getsource(cache_warm.warm)
    assert '"/api/search"' in src
