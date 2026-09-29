"""One heavy rewrite of `stories` at a time, across ALL of them.

Each bulk pass used to take its own advisory key, so `popularity_rank`
excluded a second `popularity_rank` and nothing else — while the thing it
actually contends with is whichever OTHER pass is walking the same 20M rows.

Measured on the live box 2026-09-29, two of them at once in pg_stat_activity:

    576412  active  00:05:16  UPDATE stories s  SET popularity   = ...
    585434  active  00:00:51  UPDATE stories st SET is_crossover = ...

and it needs no coincidence: `_popularity_loop` starts 15 minutes after boot
and runs ~3h51m, `_curation_loop` starts 45 minutes after boot, and both are
weekly. They collided by construction, every week, at the same offset. The
same collision took the biggest fandom hub down that morning.

Source-level assertions for the wiring, because reproducing the collision needs
two multi-hour passes; one real test for the behaviour, because a lock that
does not actually exclude is worse than none.
"""
import pathlib

import pytest

import maintenance_lock

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every pass that bulk-rewrites `stories`.
JOBS = ["popularity_rank.py", "content_gates.py", "crossover.py",
        "series_wordcount.py"]

# The per-job keys, which are NOT the shared one and must stay distinct from it.
PER_JOB_KEYS = [8_531_197_402_664_219, 0x0C205501, 0x6721C0DE, 0x5E21E5CD]


@pytest.mark.parametrize("job", JOBS)
def test_every_heavy_pass_takes_the_shared_lock(job):
    src = (ROOT / job).read_text()
    assert "maintenance_lock.heavy_pass(" in src, (
        f"{job} bulk-rewrites `stories` and must run inside "
        f"maintenance_lock.heavy_pass(); a per-job key excludes only itself")


@pytest.mark.parametrize("job", JOBS)
def test_a_dry_run_does_not_take_the_lock(job):
    """A dry run writes nothing, so it must never be blocked by the live worker.

    Otherwise `tests/test_maintenance_timeouts.py` — which calls run(dry_run=True)
    for all of these — fails or passes depending on what the box happens to be
    doing, which is the least reproducible kind of red.
    """
    src = (ROOT / job).read_text()
    i_dry = src.index("if dry_run:")
    i_lock = src.index("maintenance_lock.heavy_pass(")
    assert i_dry < i_lock, (
        f"{job} must return the dry-run result BEFORE acquiring the lock")


def test_the_shared_key_collides_with_nothing():
    assert maintenance_lock.HEAVY_KEY not in PER_JOB_KEYS
    assert len(set(PER_JOB_KEYS)) == len(PER_JOB_KEYS)


def test_holding_it_defers_the_next_pass(db):
    """The property that matters: the second caller is turned away, not queued.

    Queueing is the failure this replaces — a second pass waiting holds a
    pooled connection for as long as the first one runs, which here is up to
    four hours against a server-wide connection ceiling.
    """
    with maintenance_lock.heavy_pass("first"):
        with pytest.raises(maintenance_lock.MaintenanceDeferred):
            with maintenance_lock.heavy_pass("second"):
                pytest.fail("the second pass should not have got the lock")

    # ...and it is released afterwards, or one crash would wedge maintenance
    # until the worker was restarted.
    with maintenance_lock.heavy_pass("third"):
        pass


def test_it_is_released_even_when_the_pass_raises(db):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with maintenance_lock.heavy_pass("explodes"):
            raise Boom()

    with maintenance_lock.heavy_pass("after"):
        pass
