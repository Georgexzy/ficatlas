"""disk_trend: a rate needs a day of samples; one-offs age out after 30 days."""
from datetime import datetime, timedelta, timezone

import disk_trend as dt

G = 1024 ** 3
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def s(days, used, db=40 * G, total=259 * G):
    return [(T0 + timedelta(days=days)).isoformat(), db, used, total]


def test_one_sample_has_a_size_and_no_rate():
    t = dt.trend([s(0, 200 * G)])
    assert t["free_bytes"] == 59 * G and t["used_per_day"] is None and t["full_in_days"] is None


def test_under_a_day_is_not_a_rate():
    assert dt.trend([s(0, 200 * G), s(0.5, 201 * G)])["used_per_day"] is None


def test_rate_and_days_to_full():
    t = dt.trend([s(0, 200 * G), s(10, 210 * G)])
    assert round(t["used_per_day"] / G, 3) == 1.0
    assert t["full_in_days"] == 49            # 49 GB free at 1 GB a day
    assert t["ninety_in_days"] == round((0.9 * 259 - 210))


def test_shrinking_disk_never_projects_a_fill_date():
    t = dt.trend([s(0, 210 * G), s(5, 195 * G)])
    assert t["used_per_day"] < 0 and t["full_in_days"] is None


def test_old_one_off_ages_out_of_the_window():
    # A 30GB cleanup 60 days ago must not bend today's projection.
    samples = [s(0, 230 * G), s(1, 200 * G)] + [s(d, 200 * G + (d - 1) * G // 2) for d in range(2, 62)]
    t = dt.trend(samples)
    assert 0.4 < t["used_per_day"] / G < 0.6
