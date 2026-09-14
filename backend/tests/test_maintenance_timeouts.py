"""A maintenance pass must re-assert `statement_timeout = 0` every batch.

`statement_timeout` is a CONNECT-TIME parameter here — `db/session.py` sets it
in `connect_args` — so `SET statement_timeout = 0` lasts exactly as long as the
connection does. `pool_recycle` is 1800s, so half an hour into a pass the pool
hands back a fresh connection carrying the DEFAULT timeout and the next batch
dies with "canceling statement due to statement timeout" having done nothing
wrong.

Measured, and it is not subtle once you know to look: the content-gate backfill
died at exactly 30 minutes, the series word-count fill at ~40, and both had set
the timeout correctly at the top of the run. `popularity_rank.py` had already
recorded the same trap from the other end — a pass that had just spent 3h45m
writing 2.4M rows could not afterwards run a 60-second count.

A source-level assertion, because the failure needs a thirty-minute run to
reproduce and no unit test is going to wait for one.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every script that loops over batches of the 20.5M-row table.
JOBS = ["series_wordcount.py", "content_gates.py", "crossover.py"]


@pytest.mark.parametrize("job", JOBS)
def test_the_timeout_is_lifted_inside_the_loop(job):
    src = (ROOT / job).read_text()
    assert "lift_statement_timeout" in src, (
        f"{job} must call lift_statement_timeout(db) before each batch")
    # Inside a `while`, not only before it. Crudely but reliably: the call has
    # to appear at a deeper indent than some `while`.
    lines = src.splitlines()
    whiles = [i for i, l in enumerate(lines) if re.match(r"\s*while\b", l)]
    calls = [i for i, l in enumerate(lines) if "lift_statement_timeout(db)" in l]
    assert calls, f"{job} never calls it"
    assert any(c > w and (len(l) - len(l.lstrip())) >
               (len(lines[w]) - len(lines[w].lstrip()))
               for c in calls for w, l in [(w, lines[c]) for w in whiles]), (
        f"{job} calls lift_statement_timeout outside its batch loop, which is "
        f"the bug this test exists for")


def test_the_helper_exists_and_is_shared():
    """One helper, not three copies — the same argument `is_bot` having one
    home already settles."""
    from db.session import lift_statement_timeout
    assert callable(lift_statement_timeout)


# ---------------------------------------------------------------------------

@pytest.mark.parametrize("job", ["content_gates", "series_wordcount", "crossover"])
def test_the_job_actually_runs(job, db):
    """IMPORTING a module proves nothing about whether its main function runs.

    `content_gates.py` shipped with `from db.session import db_session` and
    `log = logging.getLogger(...)` deleted — both had been sitting inside a
    block that was replaced wholesale when the term lists moved to
    `gate_terms.py`. The module still imported cleanly, because nothing at
    module level referenced either name, so every test passed and the failure
    waited until the job ran unattended at 07:00:

        NameError: name 'db_session' is not defined

    A dry run is the cheapest possible proof that the entry point is wired up:
    it takes the same path through imports, session, logging and SQL as the
    real thing and writes nothing.
    """
    mod = __import__(job)
    result = mod.run(dry_run=True)
    assert isinstance(result, dict) and result, f"{job}.run(dry_run=True) returned {result!r}"
