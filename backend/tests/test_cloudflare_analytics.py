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
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t" * 40)
    monkeypatch.setenv("CLOUDFLARE_ZONE_ID", "z" * 32)
    yield
    cf._cache.clear()


def test_unconfigured_names_which_credential_is_missing(monkeypatch):
    """Compose interpolates from the shell before .env, so one of the two can
    arrive without anyone setting it here -- measured: a 10-character
    placeholder in ~/.bashrc overrode a working token. "Not connected" would
    send someone looking for a token they already have."""
    monkeypatch.delenv("CLOUDFLARE_ZONE_ID")
    r = cf.fetch(7)
    assert r["configured"] is False
    assert r["missing"] == ["CLOUDFLARE_ZONE_ID"]
    assert "CLOUDFLARE_ZONE_ID" in r["reason"]


def test_a_graphql_error_is_passed_through_verbatim(monkeypatch):
    """GraphQL answers 200 with an errors array, so a failure looks like a
    success to anything checking the status code."""
    monkeypatch.setattr(cf, "_post", lambda t, p: {
        "errors": [{"message": "unknown field 'pageViews'"}], "data": None})
    r = cf.fetch(7)
    assert r["error"] == "GraphQL error"
    assert "unknown field" in r["detail"]


def test_a_timeout_says_so_rather_than_looking_like_no_traffic(monkeypatch):
    def boom(t, p):
        raise TimeoutError("timed out")
    monkeypatch.setattr(cf, "_post", boom)
    r = cf.fetch(7)
    assert r["configured"] is True
    assert r["error"] == "TimeoutError"


def test_http_error_reports_the_status(monkeypatch):
    def boom(t, p):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    monkeypatch.setattr(cf, "_post", boom)
    assert cf.fetch(7)["error"] == "HTTP 403"


def test_a_zone_the_token_cannot_read_is_named_as_such(monkeypatch):
    monkeypatch.setattr(cf, "_post", lambda t, p: {"data": {"viewer": {"zones": []}}})
    assert cf.fetch(7)["error"] == "Zone not found"


def _body(groups):
    return {"data": {"viewer": {"zones": [{"httpRequests1dGroups": groups}]}}}


def test_uniques_are_a_peak_day_not_a_sum(monkeypatch):
    """Same rule as visit_events' visitor hash: the same person on two days is
    two uniques, so summing them gives a number that grows with the window
    rather than with the audience."""
    monkeypatch.setattr(cf, "_post", lambda t, p: _body([
        {"dimensions": {"date": "2026-09-01"},
         "sum": {"requests": 10, "pageViews": 5, "cachedRequests": 2, "bytes": 100,
                 "threats": 0, "countryMap": [{"clientCountryName": "US", "requests": 10}]},
         "uniq": {"uniques": 30}},
        {"dimensions": {"date": "2026-09-02"},
         "sum": {"requests": 20, "pageViews": 9, "cachedRequests": 3, "bytes": 200,
                 "threats": 1, "countryMap": [{"clientCountryName": "GB", "requests": 20}]},
         "uniq": {"uniques": 40}},
    ]))
    r = cf.fetch(2)
    assert r["totals"]["uniques"] == 40, "peak, not 70"
    assert r["totals"]["requests"] == 30
    assert r["cache_ratio"] == pytest.approx(5 / 30)
    assert [c["country"] for c in r["countries"]] == ["GB", "US"]


def test_a_second_call_inside_the_ttl_does_not_leave_the_building(monkeypatch):
    """This runs inside a request. A page reload must not become an outbound
    call to a third party."""
    calls = []
    monkeypatch.setattr(cf, "_post", lambda t, p: calls.append(1) or _body([]))
    cf.fetch(5)
    cf.fetch(5)
    assert len(calls) == 1


def test_a_failure_is_not_cached(monkeypatch):
    """Otherwise one blip hides real data for the whole TTL."""
    monkeypatch.setattr(cf, "_post", lambda t, p: {"errors": [{"message": "nope"}]})
    cf.fetch(9)
    calls = []
    monkeypatch.setattr(cf, "_post", lambda t, p: calls.append(1) or _body([]))
    cf.fetch(9)
    assert len(calls) == 1, "the error should not have been cached"
