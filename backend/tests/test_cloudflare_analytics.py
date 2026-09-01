"""The edge-analytics reader: what it does when Cloudflare does not cooperate.

Every path here except the happy one is a failure path, and they all have to end
with the admin page rendering. A traffic page that 500s because a third party is
slow is worse than one that says the third party was slow -- and this call is
made inside a request, which is the shape of the autopoll incident in CLAUDE.md.

No network: the transport is stubbed. What is tested is the handling.
"""

import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import cloudflare_analytics as cf


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cf._cache.clear()
    cf._account_id = "acc"          # discovered once; not what these test
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t" * 40)
    monkeypatch.setenv("CLOUDFLARE_ZONE_ID", "z" * 32)
    yield
    cf._cache.clear()
    cf._account_id = None


def _body(**blocks):
    base = {"daily": [], "cache": [], "countries": [], "statuses": [], "paths": []}
    base.update(blocks)
    return {"data": {"viewer": {"accounts": [base]}}}


def test_unconfigured_names_which_credential_is_missing(monkeypatch):
    """Compose interpolates from the shell before .env, so one of the two can
    arrive without anyone setting it here -- measured: a 10-character
    placeholder in ~/.bashrc overrode a working token."""
    monkeypatch.delenv("CLOUDFLARE_ZONE_ID")
    r = cf.fetch(7)
    assert r["configured"] is False
    assert r["missing"] == ["CLOUDFLARE_ZONE_ID"]


def test_a_permission_error_says_what_to_do(monkeypatch):
    """Cloudflare states this as an actor id and a permission string, which is
    accurate and unusable. It happens because token permissions REPLACE rather
    than add -- measured here twice."""
    monkeypatch.setattr(cf, "_post", lambda t, p: {"errors": [{"message":
        "Actor 'com.cloudflare.api.token.722c' does not have permission "
        "'com.cloudflare.api.account.zone.analytics.read' for zone d7c"}]})
    r = cf.fetch(7)
    assert r["error"] == "The API token cannot read analytics"
    assert "Analytics" in r["fix"]
    assert "does not have permission" in r["detail"]


def test_other_graphql_errors_are_passed_through_verbatim(monkeypatch):
    """A schema that has moved on has to say so precisely; guessing at field
    names from outside is how this would stay broken quietly."""
    monkeypatch.setattr(cf, "_post", lambda t, p: {
        "errors": [{"message": "unknown field 'edgeResponseBytes'"}]})
    r = cf.fetch(7)
    assert r["error"] == "GraphQL error"
    assert "unknown field" in r["detail"]


def test_a_timeout_says_so_rather_than_looking_like_no_traffic(monkeypatch):
    monkeypatch.setattr(cf, "_post", lambda t, p: (_ for _ in ()).throw(TimeoutError("x")))
    r = cf.fetch(7)
    assert r["configured"] is True and r["error"] == "TimeoutError"


def test_http_error_reports_the_status(monkeypatch):
    def boom(t, p):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    monkeypatch.setattr(cf, "_post", boom)
    assert cf.fetch(7)["error"] == "HTTP 403"


def test_a_token_that_cannot_list_accounts_is_named(monkeypatch):
    cf._account_id = None
    monkeypatch.setattr(cf, "_get", lambda u, t: (_ for _ in ()).throw(RuntimeError("no")))
    assert cf.fetch(7)["error"] == "The API token cannot list accounts"


def test_only_a_real_hit_counts_as_cached(monkeypatch):
    """Cloudflare reports several statuses that are not a hit. Counting
    `revalidated` or `expired` as cached would flatter the figure -- only `hit`
    was answered without asking this server."""
    monkeypatch.setattr(cf, "_post", lambda t, p: _body(
        daily=[{"dimensions": {"date": "2026-09-01"}, "count": 100,
                "sum": {"edgeResponseBytes": 500}}],
        cache=[{"dimensions": {"cacheStatus": "dynamic"}, "count": 70},
               {"dimensions": {"cacheStatus": "hit"}, "count": 20},
               {"dimensions": {"cacheStatus": "revalidated"}, "count": 10}],
    ))
    r = cf.fetch(1)
    assert r["totals"]["cache_hits"] == 20
    assert r["cache_ratio"] == pytest.approx(0.2)


def test_server_and_client_errors_are_counted_apart(monkeypatch):
    """A 404 on a withdrawn work and a 500 are not the same news."""
    monkeypatch.setattr(cf, "_post", lambda t, p: _body(
        daily=[{"dimensions": {"date": "2026-09-01"}, "count": 10, "sum": {"edgeResponseBytes": 1}}],
        statuses=[{"dimensions": {"edgeResponseStatus": 200}, "count": 900},
                  {"dimensions": {"edgeResponseStatus": 404}, "count": 40},
                  {"dimensions": {"edgeResponseStatus": 500}, "count": 5},
                  {"dimensions": {"edgeResponseStatus": 502}, "count": 2}],
    ))
    t = cf.fetch(1)["totals"]
    assert t["server_errors"] == 7 and t["client_errors"] == 40


def test_a_second_call_inside_the_ttl_does_not_leave_the_building(monkeypatch):
    """This runs inside a request. A page reload must not become an outbound
    call to a third party."""
    calls = []
    monkeypatch.setattr(cf, "_post", lambda t, p: calls.append(1) or _body())
    cf.fetch(5); cf.fetch(5)
    assert len(calls) == 1


def test_a_failure_is_not_cached(monkeypatch):
    """Otherwise one blip hides real data for the whole TTL."""
    monkeypatch.setattr(cf, "_post", lambda t, p: {"errors": [{"message": "nope"}]})
    cf.fetch(9)
    calls = []
    monkeypatch.setattr(cf, "_post", lambda t, p: calls.append(1) or _body())
    cf.fetch(9)
    assert len(calls) == 1, "the error should not have been cached"
