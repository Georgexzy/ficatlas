"""The series stale sweep must not run on a listing it did not finish reading.

`fill_one` renumbers every held work whose position is above the last one the
listing accounted for. That is right when the listing was read to its end and
destructive when it was not: a 525 or a rate-limit on page 2 of a 60-work
series is indistinguishable, from inside, from a complete 20-work series — and
the sweep then rewrites the author's own positions for works 21..60 into
publication order. The stated positions are gone at that point; nothing records
what they were.

The same applies to running out of MAX_PAGES, which is the ordinary outcome for
any series with more than 100 members.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_series_fill


class FakeDB:
    """Records the SQL it is asked to run, so the sweep is observable."""

    def __init__(self):
        self.statements = []

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        return self

    def scalar(self):
        return "A series"

    def swept(self):
        return any("UPDATE series_works" in s for s in self.statements)


def _fill(monkeypatch, complete):
    db = FakeDB()
    monkeypatch.setattr(ao3_series_fill, "persist_live_results",
                        lambda *a, **k: 0, raising=False)
    import live_fetch.persist as persist
    monkeypatch.setattr(persist, "persist_live_results", lambda *a, **k: 0)
    import ao3_series
    monkeypatch.setattr(ao3_series, "record", lambda *a, **k: 0)
    entries = [{"url": f"https://archiveofourown.org/works/{i}"} for i in range(1, 21)]
    ao3_series_fill.fill_one(db, "series-1", "ao3:99", entries, complete)
    return db


def test_a_partial_listing_does_not_renumber(monkeypatch):
    assert not _fill(monkeypatch, complete=False).swept()


def test_a_complete_listing_still_tidies_up(monkeypatch):
    assert _fill(monkeypatch, complete=True).swept()


def test_complete_defaults_to_false():
    """A caller that has not thought about it must get the safe behaviour."""
    import inspect
    sig = inspect.signature(ao3_series_fill.fill_one)
    assert sig.parameters["complete"].default is False
