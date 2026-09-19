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
