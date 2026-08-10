"""The circuit breaker that is supposed to stop crawling a blocked archive.

It had a hole exactly where it was needed most. The breaker counted blocked
failures inside a six-hour window and tripped at five — which catches a sudden
burst, and cannot catch the case that actually happens. A permanently blocked
site does not fail in a burst; it fails once per crawl interval, forever. With a
crawl every few hours, at most one or two failures ever landed in a window.

FanFiction.net returned 403 sixty-three consecutive times over a fortnight, was
retried on schedule throughout, and stayed "enabled" the entire time.

So the streak is counted too: consecutive failures are independent of how often
we try, which is the property the window lacked.
"""
import pytest

import scheduler


class _Job:
    def __init__(self, status, error=None, job_type="incremental"):
        self.status = status
        self.error = error
        self.job_type = job_type


class _Query:
    """Just enough of the SQLAlchemy chain that _consecutive_blocked uses."""
    def __init__(self, jobs): self._jobs = jobs
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def limit(self, n): self._jobs = self._jobs[:n]; return self
    def all(self): return self._jobs


class _DB:
    def __init__(self, jobs): self._jobs = jobs
    def query(self, *a, **k): return _Query(list(self._jobs))


def blocked(n):
    return [_Job("failed", "[blocked] Client error '403 Forbidden'") for _ in range(n)]


def transient(n):
    return [_Job("failed", "[transient] ReadTimeout") for _ in range(n)]


def ok(n):
    return [_Job("done") for _ in range(n)]


class TestConsecutiveBlocked:
    def test_no_history_is_no_streak(self):
        assert scheduler._consecutive_blocked(_DB([]), "ffnet") == 0

    def test_counts_a_run_of_blocks(self):
        assert scheduler._consecutive_blocked(_DB(blocked(6)), "ffnet") == 6

    def test_the_ffnet_case(self):
        """Sixty-three 403s in a row, spread over a fortnight. The window-based
        count saw at most one or two of these at a time."""
        streak = scheduler._consecutive_blocked(_DB(blocked(63)), "ffnet")
        assert streak >= scheduler.CRAWL_FAIL_THRESHOLD

    def test_a_success_ends_the_streak(self):
        # Newest first: two blocks, then a success, then more blocks. Only the
        # two most recent count — the site demonstrably worked after the others.
        jobs = blocked(2) + ok(1) + blocked(5)
        assert scheduler._consecutive_blocked(_DB(jobs), "ffnet") == 2

    def test_a_recent_success_means_no_trip(self):
        jobs = ok(1) + blocked(20)
        assert scheduler._consecutive_blocked(_DB(jobs), "ffnet") == 0

    def test_transient_failures_neither_extend_nor_break_it(self):
        """AO3 being slow in the middle of a run of 403s says nothing either
        way, so a timeout must not reset the streak or pad it."""
        jobs = blocked(3) + transient(2) + blocked(2)
        assert scheduler._consecutive_blocked(_DB(jobs), "ffnet") == 5

    def test_a_timeout_alone_is_not_a_block(self):
        """The distinction the whole breaker rests on: a site that is merely
        slow must never be disabled."""
        assert scheduler._consecutive_blocked(_DB(transient(10)), "ao3") == 0

    def test_only_looks_at_recent_history(self):
        assert scheduler._consecutive_blocked(_DB(blocked(50)), "ffnet", look=8) == 8

    def test_survives_a_broken_query(self):
        class _Boom:
            def query(self, *a, **k): raise RuntimeError("db gone")
        # A breaker that raises would take out the crawl error handler itself.
        assert scheduler._consecutive_blocked(_Boom(), "ffnet") == 0


class TestThreshold:
    def test_the_streak_reaches_the_trip_point(self):
        assert scheduler._consecutive_blocked(_DB(blocked(scheduler.CRAWL_FAIL_THRESHOLD)),
                                              "ffnet") >= scheduler.CRAWL_FAIL_THRESHOLD

    def test_one_short_does_not(self):
        n = scheduler.CRAWL_FAIL_THRESHOLD - 1
        assert scheduler._consecutive_blocked(_DB(blocked(n)), "ffnet") < scheduler.CRAWL_FAIL_THRESHOLD
