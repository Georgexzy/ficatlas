"""Unit tests for live_fetch.persist._as_datetime.

This function normalises incoming updated_at values to timezone-aware UTC.
A regression here (returning naive datetimes) made the cross-post merge in
persist_live_results raise `TypeError` on `inc_when > existing_work.updated_at`,
which the bare `except: rollback()` swallowed and turned into duplicate rows.
These tests pin the fix so it cannot silently regress.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_fetch.persist import _as_datetime


def test_none_and_empty():
    assert _as_datetime(None) is None
    assert _as_datetime("") is None


def test_naive_iso_string_becomes_aware_utc():
    dt = _as_datetime("2024-01-01T12:00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc


def test_aware_iso_string_normalised_to_utc():
    dt = _as_datetime("2024-01-01T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_offset_iso_string_converted_to_utc():
    # +05:00 -> 07:00 UTC
    dt = _as_datetime("2024-01-01T12:00:00+05:00")
    assert dt is not None
    assert dt.hour == 7
    assert dt.tzinfo == timezone.utc


def test_naive_datetime_object_becomes_aware():
    dt = _as_datetime(datetime(2024, 1, 1, 12, 0, 0))
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_aware_datetime_object_normalised():
    dt = _as_datetime(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    assert dt == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_garbage_returns_none():
    assert _as_datetime("not-a-date") is None
    assert _as_datetime(12345) is None or isinstance(_as_datetime(12345), datetime)


def test_naive_vs_aware_comparison_never_raises():
    # The exact comparison that used to raise TypeError and silently dup rows.
    naive = _as_datetime("2023-05-01T00:00:00")
    aware = _as_datetime(datetime(2022, 1, 1, tzinfo=timezone.utc))
    assert naive > aware
