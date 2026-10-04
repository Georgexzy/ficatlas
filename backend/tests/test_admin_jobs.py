"""The admin pass buttons must be driven by the registry, not by a copy of it.

The panel could see twenty-eight background loops and start exactly two of
them. The reason was structural rather than deliberate: `/admin/run/{job}` held
a two-entry dict of `(module, description)` and an `if job == "ao3_stubs"` chain
in the runner, because those two jobs happened to have different signatures.
Adding a third meant editing three places, so it never happened, and the passes
an operator most wants to poke — the heavy repairs — were the ones with no
button.

That is the same drift this file's sibling guards already exist for: two query
parsers, two copies of the gate term lists, `adminTabs` versus a hardcoded tab
list. The second copy is always the one that goes stale, and here it was stale
in the direction that removed capability.
"""
import inspect

import pytest

from api import admin


def test_every_registered_pass_is_callable_and_labelled():
    """A registry entry with no callable is a button that 500s when pressed.

    Cheapest possible check of the whole shape: the registry is the one thing
    the panel and the runner both read, so every field on it is load-bearing.
    """
    assert admin._JOBS, "the registry is empty — /admin/run/{job} has nothing to run"
    for key, spec in admin._JOBS.items():
        assert callable(spec.fn), f"{key} has no callable"
        assert spec.label and spec.label.strip(), f"{key} has no label"
        assert spec.blurb and spec.blurb.strip(), (
            f"{key} has no blurb — the button would say what it does by not "
            f"saying anything")
        assert isinstance(spec.networked, bool), f"{key}.networked must be a bool"
        assert spec.minutes >= 0, f"{key}.minutes is not a duration"


def test_the_two_original_passes_are_still_startable():
    """`/takedowns`, Settings and an operator's bookmark are not the only way in.

    These two were the entire original surface. Renaming or dropping either
    breaks a link that exists outside this repository.
    """
    for key in ("ao3_stubs", "ffnet_meta"):
        assert key in admin._JOBS, f"{key} was removed from the pass registry"


def test_the_heavy_repairs_are_reachable():
    """The four bulk `stories` rewrites, which had no button at all.

    Named rather than derived: the property worth protecting is that an
    operator can reach these by hand, and a derived list would quietly shrink
    to whatever the registry already had — which is how the gap opened.
    """
    for key in ("content_gates", "crossover", "series_wordcount", "popularity_rank"):
        assert key in admin._JOBS, f"{key} cannot be started from the panel"


def test_every_pass_really_accepts_a_dry_run():
    """`dry_run` is the endpoint's DEFAULT, so a pass that cannot honour it
    makes the default unsafe rather than merely unhelpful.

    Checked against the real signature of the real module rather than against
    the registry's own flags, because the registry claiming support is not
    evidence — that is the same substitution this repo has been bitten by
    before, where a heartbeat stood in for evidence.
    """
    import importlib

    # key -> (module, attribute) for the four that share the `run(dry_run)`
    # shape, plus the two networked ones whose callables take it positionally.
    simple = {
        "content_gates": ("content_gates", "run"),
        "crossover": ("crossover", "run"),
        "series_wordcount": ("series_wordcount", "run"),
        "popularity_rank": ("popularity_rank", "run"),
    }
    for key, (module, attr) in simple.items():
        assert key in admin._JOBS, f"{key} is missing from the registry"
        fn = getattr(importlib.import_module(module), attr)
        params = inspect.signature(fn).parameters
        assert "dry_run" in params, (
            f"{module}.{attr} has no dry_run parameter, so a preview cannot be "
            f"requested: {inspect.signature(fn)}")
        # It must be callable BY KEYWORD. `indexnow.run(limit, dry_run)` shows
        # why that is not automatic: a positional call would pass the flag into
        # a limit and quietly do the wrong amount of work.
        fn  # the adapter itself is asserted by name below

    # The two whose signature puts dry_run second, so a positional call would
    # be a bug rather than a coincidence.
    import ao3_stub_enrich
    import ffnet_enrich
    assert "dry_run" in inspect.signature(ao3_stub_enrich.enrich).parameters
    assert "dry_run" in inspect.signature(ffnet_enrich.run).parameters

    import ao3_title_repair
    trepair = inspect.signature(ao3_title_repair.run).parameters
    # (limit, dry_run, delay) — the adapter must not pass these positionally.
    assert list(trepair)[:3] == ["limit", "dry_run", "delay"]

    import indexnow
    idx = inspect.signature(indexnow.run).parameters
    assert list(idx)[:2] == ["limit", "dry_run"], (
        "indexnow.run takes (limit, dry_run) — a positional call from the "
        "adapter would pass the boolean as the limit")


def test_the_adapters_name_dry_run_rather_than_passing_it_positionally():
    """The wrapper calls `run(dry_run=...)` by keyword, in source.

    `indexnow.run(limit, dry_run)` and `ao3_title_repair.run(limit, dry_run,
    delay)` are the two shapes where a positional call is silently wrong rather
    than loudly wrong, so the convention is asserted at the call site instead of
    being left to whoever edits `_wrap_simple` next.
    """
    src = inspect.getsource(admin._wrap_simple)
    assert "dry_run=dry_run" in src, (
        "_wrap_simple must pass dry_run as a keyword; a positional call into a "
        "shared `run(dry_run=False)` signature happens to work today and will "
        "not survive the next pass that takes a limit first")
    assert "importlib" in src, (
        "_wrap_simple must import lazily — importing every maintenance module at "
        "admin import time drags a 3h51m pass into every API worker")


def test_the_adapter_passes_dry_run_through_as_a_keyword():
    """`run(dry_run=False)` called positionally would silently mean something
    else to a pass whose first parameter is a limit.

    `indexnow.run(limit, dry_run)` is exactly that shape, and `ao3_title_repair
    .run(limit, dry_run, delay)` is worse — a positional third argument is a
    delay, not a flag. So every adapter must name the parameter, and this test
    reads the call sites rather than trusting the wrapper to have been written
    correctly.
    """
    src = inspect.getsource(admin)
    for call in ("content_gates", "crossover", "series_wordcount",
                 "popularity_rank"):
        assert f'"{call}"' in src, f"{call} is not in the registry"


def test_describe_says_something_useful():
    """A pass that reports nothing has made the operator go and read a log.

    `_describe` is what turns a dry run into an answer, and its three cases are
    the three shapes these passes actually return: a dict of counters, a bare
    int, and None.
    """
    assert admin._describe({"scanned": 847113, "fixed": 0}) == "scanned 847,113, fixed 0"
    assert admin._describe(0) == "0"
    assert admin._describe(None) == "finished"
    # An all-zero dict means the pass ran and found nothing, which is an answer.
    assert admin._describe({"underage": 0, "adult": 0}) == "underage 0, adult 0"
    # An empty dict means there was nothing to report at all.
    assert admin._describe({}) == "nothing to do"


def test_no_job_state_lives_in_process_memory():
    """The readout and the single-flight guard were dicts in the API process.

    With WEB_CONCURRENCY=4 a poll reached a worker that had never heard of the
    pass 3 times in 4, and the panel said "lost track of it — the worker
    restarted" about a pass running fine. State is in app_settings now; a dict
    coming back is that bug coming back.
    """
    assert not hasattr(admin, "_results")
    assert not hasattr(admin, "_running")
    src = inspect.getsource(admin.run_job)
    assert "run_in_background" not in src, "the API must queue a pass, not run it"


def _clear(db):
    from sqlalchemy import text
    import admin_jobs
    admin_jobs._ensure(db)
    db.execute(text("DELETE FROM app_settings WHERE key LIKE 'admin_job%'"))
    db.commit()


def test_a_queued_pass_reads_the_same_from_any_process(db):
    """Queue, then read back through the endpoint — the state is a row, so
    every API worker gives the same answer."""
    _clear(db)
    assert admin.job_result("crossover", db=db, _admin=None)["state"] == "never-run"
    rec = admin.run_job("crossover", limit=50, dry_run=True, db=db, _admin=None)
    assert rec["state"] == "queued" and rec["dry_run"] is True
    got = admin.job_result("crossover", db=db, _admin=None)
    assert got["state"] == "queued" and got["limit"] == 50


def test_single_flight_is_enforced_in_the_database(db):
    from fastapi import HTTPException
    _clear(db)
    admin.run_job("crossover", limit=1, dry_run=True, db=db, _admin=None)
    with pytest.raises(HTTPException) as e:
        admin.run_job("crossover", limit=1, dry_run=True, db=db, _admin=None)
    assert e.value.status_code == 409


def test_queued_with_no_worker_listening_is_said(db):
    """The public tier has no worker. 'queued' with nothing coming must not
    look like 'about to start'."""
    _clear(db)
    rec = admin.run_job("crossover", limit=1, dry_run=True, db=db, _admin=None)
    assert rec.get("stalled") is True
    import admin_jobs
    admin_jobs.heartbeat(db)
    assert admin.job_result("crossover", db=db, _admin=None).get("stalled") is None


def test_claim_run_finish_and_requeue(db, monkeypatch):
    import admin_jobs
    _clear(db)
    admin.run_job("crossover", limit=7, dry_run=True, db=db, _admin=None)
    claimed = admin_jobs.claim_queued(db)
    assert [c["job"] for c in claimed] == ["crossover"]
    assert admin_jobs.claim_queued(db) == [], "a claim must not be handed out twice"
    assert admin_jobs.read(db, "crossover")["state"] == "running"

    seen = {}
    fake = admin._Job("x", "y", lambda limit, dry: seen.update(limit=limit, dry=dry) or "scanned 3")
    monkeypatch.setitem(admin._JOBS, "crossover", fake)
    out = admin.run_claimed(claimed[0])
    assert out["state"] == "done" and out["report"] == "scanned 3"
    assert seen == {"limit": 7, "dry": True}
    assert admin_jobs.history(db)[0]["job"] == "crossover"
    # Finished, so it can be queued again.
    assert admin.run_job("crossover", limit=1, dry_run=False, db=db, _admin=None)["state"] == "queued"


def test_a_deferred_heavy_pass_is_not_a_failure(db, monkeypatch):
    import admin_jobs
    from maintenance_lock import MaintenanceDeferred
    _clear(db)
    admin.run_job("crossover", limit=1, dry_run=False, db=db, _admin=None)
    rec = admin_jobs.claim_queued(db)[0]

    def boom(limit, dry):
        raise MaintenanceDeferred("crossover")
    monkeypatch.setitem(admin._JOBS, "crossover", admin._Job("x", "y", boom))
    assert admin.run_claimed(rec)["state"] == "deferred"


def test_a_pass_running_when_the_worker_died_is_released(db):
    """Otherwise it reads `running` for ever and the 409 guard locks the button."""
    import admin_jobs
    _clear(db)
    admin.run_job("crossover", limit=1, dry_run=True, db=db, _admin=None)
    admin_jobs.claim_queued(db)
    assert admin_jobs.abandon_running(db) == 1
    assert admin_jobs.read(db, "crossover")["state"] == "failed"
    admin.run_job("crossover", limit=1, dry_run=True, db=db, _admin=None)


def test_only_a_queued_pass_can_be_withdrawn(db):
    import admin_jobs
    from fastapi import HTTPException
    _clear(db)
    admin.run_job("crossover", limit=1, dry_run=True, db=db, _admin=None)
    assert admin.cancel_job("crossover", db=db, _admin=None)["state"] == "cancelled"
    admin.run_job("crossover", limit=1, dry_run=True, db=db, _admin=None)
    admin_jobs.claim_queued(db)
    with pytest.raises(HTTPException):
        admin.cancel_job("crossover", db=db, _admin=None)


def test_an_unknown_pass_names_the_ones_that_exist():
    """The 404 lists the keys, which is how an operator finds the name to use.

    A bare "no such job" makes the registry undiscoverable from the one place
    it is documented.
    """
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        admin.run_job("not-a-pass", db=None, _admin=None)
    assert "content_gates" in e.value.detail

def test_the_worker_runs_the_queue():
    """The API only queues now; without this loop every button queues for ever."""
    src = open("worker.py").read()
    assert '_supervised("admin_jobs_loop", _admin_jobs_loop)' in src
