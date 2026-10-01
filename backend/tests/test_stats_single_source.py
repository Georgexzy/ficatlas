"""The index's own numbers have to add up.

`/api/stats/totals` reports how many works there are AND how many of them came
from each archive, and it renders both: the header pill carries the total, and
the per-archive bars sit directly beneath it in the index status panel. A reader
can add those bars up. That makes the pair a single claim about the index rather
than two figures that happen to be printed near each other.

They were measured separately — `count(*)` in the totals scan, and
`GROUP BY site` in the sites scan, each with its own lock, cache, persisted copy
and recompute moment — and two scans of a table the crawler is writing to do not
agree. Measured on the live index with every cache warm, in one response: the
totals count 20,852,026, the breakdown beside it summed to 20,851,342, and a
fresher breakdown for the same endpoint summed to 20,852,285.

The signs are the part that matters. One was behind the total and one was ahead
of it, simultaneously, because they were taken at different times. So neither
figure was stale in a way a refresh would fix, and the site could show a
breakdown that added up to a different number than the headline above it.
"""
import pytest

import api.stats as stats


# Three rows, 684 apart from the figure count(*) reported — the exact gap and
# the exact shape measured on the live index, so these tests are exercising the
# real disagreement rather than a tidy one.
SCAN = {"stories": 20_852_026, "hosted": 29_953, "total_words": 1}
SITES = [
    {"site": "ao3", "count": 14_242_114, "last_indexed": "2026-10-01T21:14:55+00:00"},
    {"site": "ffnet", "count": 6_579_279, "last_indexed": "2026-10-01T21:19:05+00:00"},
    {"site": "fictionalley", "count": 29_949, "last_indexed": "2026-06-11T20:25:09+00:00"},
]


@pytest.fixture
def rows(monkeypatch):
    """Stand in for the cached sites rows, so no test touches the cache or a DB.

    Stubs the accessor rather than taking a parameter on `_with_sites`: the
    function's whole argument is "here is the payload you already have", and
    giving it a second way in is a second thing to keep correct.
    """
    def install(value):
        monkeypatch.setattr(stats, "_site_rows_without_scanning", lambda: value)
    return install


# ── The invariant ────────────────────────────────────────────────────────────

def test_the_total_equals_the_sum_of_the_per_archive_figures(rows):
    """The property the whole change exists to establish."""
    rows(SITES)
    out = stats._with_sites(dict(SCAN))
    assert sum(out["sites"].values()) == out["stories"]


def test_the_total_is_derived_not_copied(rows):
    """Deliberately built so the scanned count and the sum DISAGREE by 684 rows.

    If this ever passed because the two happened to be equal, the test would no
    longer be testing the derivation — which is the only part that matters.
    """
    rows(SITES)
    out = stats._with_sites(dict(SCAN))
    assert out["stories_scanned"] == 20_852_026      # what count(*) said
    assert out["stories"] == 20_851_342              # what the breakdown sums to
    assert out["stories"] != out["stories_scanned"]


def test_the_scanned_count_is_kept_rather_than_discarded(rows):
    """It is a real measurement, and the gap between the two figures is the age
    difference between two scans — worth being able to see, and worth never
    displaying next to a breakdown that contradicts it."""
    rows(SITES)
    assert stats._with_sites(dict(SCAN))["stories_scanned"] == 20_852_026


def test_a_single_archive_still_satisfies_the_invariant(rows):
    """One row summing to one row. The failure mode was never "several archives"
    specifically; it was two scans, and a one-archive index has two as well."""
    rows([{"site": "ao3", "count": 499, "last_indexed": None}])
    out = stats._with_sites({"stories": 500})
    assert out["stories"] == 499
    assert out["stories"] == sum(out["sites"].values())


def test_the_derived_total_tracks_the_index_growing(rows):
    """Not frozen: a later scan with more rows must move the headline, or the
    site would report a constant total for a growing index."""
    small = [{"site": "ao3", "count": 100, "last_indexed": None}]
    rows(small)
    assert stats._with_sites({"stories": 100})["stories"] == 100
    rows(small + [{"site": "ffnet", "count": 7, "last_indexed": None}])
    assert stats._with_sites({"stories": 107})["stories"] == 107


# ── Absent breakdown, which is a normal state and not an error ───────────────

def test_totals_survive_a_missing_breakdown(rows):
    """`_with_sites` returns None rather than scanning — see
    `site_counts_without_scanning`, which must not start a second pass over the
    biggest table on the box. So a cold cache legitimately has totals and no
    breakdown, and the response has to stay usable."""
    rows([])
    out = stats._with_sites(dict(SCAN))
    assert out["stories"] == 20_852_026
    assert "sites" not in out


def test_nothing_is_invented_from_an_empty_payload():
    assert stats._with_sites(None) is None


def test_an_empty_breakdown_is_not_reported_as_an_index_of_nothing(rows):
    """`sites` present but empty would render as a panel reading "0 indexed"
    against a real index. Absent is the honest answer; zero is a false one."""
    rows([])
    out = stats._with_sites(dict(SCAN))
    assert out.get("stories", 0) != 0


# ── The shape must not depend on which path answered ────────────────────────

_ROW = {k: 1 for k in (
    "stories", "hosted", "total_words", "dlp", "hpffa",
    "indexed_last_hour", "indexed_last_day",
    "updated_last_month", "updated_last_quarter", "updated_last_year",
    "checked_last_week",
)}


def test_both_paths_build_the_same_payload():
    """`?refresh=1` used to build a seven-key dict while the background recompute
    built an eleven-key one, so the response SHAPE depended on which of the two
    happened to serve a given request.

    Silent, because every field in the frontend renders conditionally — a
    missing key is not an error, it is a row that quietly is not there. A
    cold-start or explicitly-refreshed response lost "Updated past 30d",
    "Re-checked past 7d" and per-archive coverage entirely.
    """
    from_background = stats._totals_from_row(_ROW, {"ao3": {"ships": 1, "characters": 1}})
    from_sync_path = stats._totals_from_row(dict(_ROW))

    assert from_background.keys() == from_sync_path.keys()
    # The keys the two paths disagreed about, named explicitly — a future
    # removal should fail here rather than on a missing row in a panel.
    for k in ("updated_last_month", "updated_last_quarter", "updated_last_year",
              "checked_last_week", "coverage"):
        assert k in from_sync_path, f"?refresh=1 would drop {k}"


def test_coverage_is_always_present_even_when_the_sample_failed():
    """`_compute_coverage` returns {} rather than raising. Rendered
    conditionally downstream, so an absent key is a silently missing bubble."""
    assert stats._totals_from_row(dict(_ROW))["coverage"] == {}


# ── Freshness travels with the breakdown, not from a second request ──────────

def test_last_indexed_is_carried_alongside_the_counts(rows):
    """The index status panel used to fetch /api/stats/sites separately for
    this, which is the request that let its own bars disagree with its own
    headline. One payload, one scan."""
    rows(SITES)
    out = stats._with_sites(dict(SCAN))
    assert out["sites_updated_at"] == {
        "ao3": "2026-10-01T21:14:55+00:00",
        "ffnet": "2026-10-01T21:19:05+00:00",
        "fictionalley": "2026-06-11T20:25:09+00:00",
    }


def test_an_archive_that_never_changed_omits_its_timestamp(rows):
    """FictionAlley closed in 2024 and has not been added to since. A null
    timestamp renders as "never" downstream, and inventing one would claim
    otherwise."""
    rows([{"site": "fictionalley", "count": 1, "last_indexed": None}])
    assert stats._with_sites({"stories": 1})["sites_updated_at"] == {}


# ── The admin panel's caller keeps working ──────────────────────────────────

def test_site_counts_without_scanning_still_serves_admin(monkeypatch):
    """`api/admin.py` calls this for its coverage section. It was refactored to
    read the raw rows once so `/totals` could take the timestamps from the same
    scan, and an admin panel that cannot read the index is a silent breakage."""
    monkeypatch.setattr(stats, "_site_rows_without_scanning", lambda: SITES)
    counts = stats.site_counts_without_scanning()
    assert counts == {"ao3": 14_242_114, "ffnet": 6_579_279, "fictionalley": 29_949}
    assert sum(counts.values()) == 20_851_342


def test_site_counts_returns_none_rather_than_scanning(monkeypatch):
    """The documented contract: a caller that cannot get the numbers for free
    gets None, never a second pass over a 20.5M-row table."""
    monkeypatch.setattr(stats, "_site_rows_without_scanning", lambda: None)
    assert stats.site_counts_without_scanning() is None
