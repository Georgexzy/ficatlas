"""The facets rebuild must have exactly one implementation.

`facets` is the vocabulary every tag, character and fandom filter resolves
against. For as long as the site was up it was rebuilt only by an admin pressing
a button, and it had drifted to 411,276 missing tag values — a fifth of the real
vocabulary.

Now a daily worker loop rebuilds it, and the admin button still exists for the
day something big is imported. Two callers means two ways to write a procedure
that drops and recreates the table every search depends on, and the copy that
drifts is the one nobody reads. This asserts the route is a wrapper, so a second
copy cannot be added without failing here.
"""
import inspect

from api import stats as stats_api
from worker import _facets_loop


def test_the_admin_route_delegates_rather_than_rebuilding():
    src = inspect.getsource(stats_api.refresh_facets)
    assert "rebuild_facets(db, min_count)" in src, (
        "POST /refresh-facets must call rebuild_facets(), not carry its own copy"
    )
    # And it must not reach for the table itself.
    for forbidden in ("CREATE TABLE facets_rebuild", "unnest(", "GROUP BY v"):
        assert forbidden not in src, f"the route must not build facets itself: {forbidden}"


def test_the_worker_loop_uses_the_same_function():
    src = inspect.getsource(_facets_loop)
    assert "rebuild_facets(" in src, "the loop must call the shared builder"
    for forbidden in ("CREATE TABLE facets_rebuild", "unnest("):
        assert forbidden not in src, f"the loop must not build facets itself: {forbidden}"


def test_the_loop_defers_rather_than_contending_for_the_heavy_slot():
    """Two heavy passes at once measured Postgres at 73% CPU with cold searches
    at 5-10s against 1.5-3.5s on a quiet box, so this takes the shared lock and
    defers rather than waits."""
    src = inspect.getsource(_facets_loop)
    assert "heavy_pass()" in src
    assert "MaintenanceDeferred" in src


def test_the_loop_lifts_the_statement_timeout_before_a_seventy_second_scan():
    """The tags scan is 73.4s on the live index. `statement_timeout` is a
    CONNECT-time parameter set in connect_args, so a session default of 60s
    would kill the largest scan of the rebuild — having done nothing wrong."""
    src = inspect.getsource(_facets_loop)
    assert "lift_statement_timeout" in src


def test_the_loop_is_on_a_schedule_and_records_evidence():
    """A heartbeat says "I ran" and evidence says "I achieved something", and the
    failure this loop exists to prevent is the vocabulary going stale again —
    which only the age of the table catches."""
    src = inspect.getsource(_facets_loop)
    assert "FACETS_INTERVAL_HOURS" in src
    assert "facets_built_at" in src
