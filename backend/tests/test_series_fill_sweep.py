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


def test_every_outcome_records_an_attempt():
    """The queue orders by most-missing and is deterministic, so a series that
    cannot be completed sorts to the head forever unless the attempt is written
    down. The worst current targets all need more works than the page cap can
    fetch, so without this the loop never reaches the backlog at all."""
    import inspect
    src = inspect.getsource(ao3_series_fill.run)
    # failure, empty listing and success must each record
    assert src.count("record_attempt") >= 3
    assert "series_fill_log" in inspect.getsource(ao3_series_fill.record_attempt)
    assert "attempted_at" in inspect.getsource(ao3_series_fill.incomplete_series)


def test_the_db_work_is_moved_off_the_event_loop():
    """run() is awaited on the worker's single event loop, and psycopg2 blocks.

    Calling it inline stalled every other loop in the worker -- the crawlers,
    the refresh queue, the AO3 pacing budget -- for as long as a GROUP BY over
    series_works took. The AO3 fetches must stay ON the loop, because the
    pacing primitives are bound to it, so only the DB work moves.
    """
    import inspect
    src = inspect.getsource(ao3_series_fill.run)
    assert "asyncio.to_thread" in src
    assert "with db_session()" not in src, "a session opened inline blocks the loop"


def test_a_failed_fill_still_records_the_attempt():
    """Otherwise the series sorts back to the head of a deterministic queue and
    re-fails every cycle -- the starvation the log table exists to prevent."""
    import inspect
    src = inspect.getsource(ao3_series_fill.run)
    fail_block = src[src.index("except Exception as e:"):]
    assert "record_attempt" in fail_block


def _story(db, wid, title, pub=None):
    from models.story import Story, SiteEnum
    s = Story(site=SiteEnum.ao3, site_id=str(wid), url=f"https://archiveofourown.org/works/{wid}",
              title=title, author="writer", word_count=1000, chapter_count=1,
              published_at=pub, is_crossover=False)
    db.add(s); db.flush()
    return str(s.id)


def _positions(db, series_id):
    from sqlalchemy import text
    return {t: p for t, p in db.execute(text("""
        SELECT st.title, sw.position FROM series_works sw JOIN stories st ON st.id = sw.story_id
         WHERE sw.series_id = :s"""), {"s": series_id})}


def test_head_cases_takes_the_positions_the_blurbs_state(db, monkeypatch):
    """Measured on the live index 2026-10-04: AO3 lists Head Cases as five works
    stating Parts 1-5, and our copy held Part 3 at 1, Part 4 at 2, Part V at 3 —
    a list missing two works had been numbered by its order."""
    from sqlalchemy import text
    import live_fetch.persist as persist
    monkeypatch.setattr(persist, "persist_live_results", lambda *a, **k: 0)
    ids = [_story(db, w, t) for w, t in [(1171045, "Ep 1"), (1754115, "Ep 2"),
                                         (11134554, "Part 3"), (19071217, "Part 4"), (25785502, "Part V")]]
    sid = db.execute(text("""INSERT INTO series (name, author, site, source, confidence, work_count, ao3_id)
        VALUES ('Head Cases', 'writer', 'ao3', 'explicit', 1, 3, 'ao3:744039') RETURNING id""")).scalar()
    for story_id, pos in zip(ids, [1, 2, 1, 2, 3]):         # exactly as stored
        db.execute(text("INSERT INTO series_works (series_id, story_id, position) VALUES (:s,:w,:p)"),
                   {"s": sid, "w": story_id, "p": pos})
    db.commit()
    # What the fill could see when it numbered them: Episodes 1 and 2 were not
    # on the page (restricted to logged-in readers), Parts 3-5 were, each
    # blurb stating its own part number.
    entries = [{"url": f"https://archiveofourown.org/works/{w}", "author": "writer",
                "series": [{"position": n, "ao3_id": "744039", "name": "Head Cases"}]}
               for n, w in [(3, 11134554), (4, 19071217), (5, 25785502)]]
    ao3_series_fill.fill_one(db, str(sid), "ao3:744039", entries, complete=True)
    db.commit()
    assert _positions(db, sid) == {"Ep 1": 1, "Ep 2": 2, "Part 3": 3, "Part 4": 4, "Part V": 5}


def test_a_removed_work_on_a_taken_number_moves_to_the_end(db, monkeypatch):
    """Damian Wayne.: AO3 lists three works; our copy also held one the author
    had removed, still at Part 1 beside the real Part 1."""
    from datetime import datetime, timezone
    from sqlalchemy import text
    import live_fetch.persist as persist
    monkeypatch.setattr(persist, "persist_live_results", lambda *a, **k: 0)
    gone = _story(db, 80625076, "The unwanted collection.", datetime(2025, 1, 1, tzinfo=timezone.utc))
    hidden = _story(db, 70000000, "Restricted, still a member")
    listed = [_story(db, w, t) for w, t in [(76115276, "Lazarus"), (79100421, "Gotham"), (81584406, "Never said")]]
    sid = db.execute(text("""INSERT INTO series (name, author, site, source, confidence, work_count, ao3_id)
        VALUES ('Damian Wayne.', 'writer', 'ao3', 'explicit', 1, 4, 'ao3:5988581') RETURNING id""")).scalar()
    for story_id, pos in [(gone, 1), (hidden, 2), (listed[0], 1), (listed[1], 3), (listed[2], 4)]:
        db.execute(text("INSERT INTO series_works (series_id, story_id, position) VALUES (:s,:w,:p)"),
                   {"s": sid, "w": story_id, "p": pos})
    db.commit()
    # AO3 states parts 1, 3, 4 — part 2 is the restricted work it does not show us.
    entries = [{"url": f"https://archiveofourown.org/works/{w}", "author": "writer",
                "series": [{"position": n, "ao3_id": "5988581", "name": "Damian Wayne."}]}
               for n, w in [(1, 76115276), (3, 79100421), (4, 81584406)]]
    ao3_series_fill.fill_one(db, str(sid), "ao3:5988581", entries, complete=True)
    db.commit()
    got = _positions(db, sid)
    assert got["Lazarus"] == 1 and got["Gotham"] == 3 and got["Never said"] == 4
    assert got["Restricted, still a member"] == 2      # unlisted but on a free number: kept
    assert got["The unwanted collection."] == 5        # unlisted on a taken number: moved after
    assert len(set(got.values())) == len(got)
