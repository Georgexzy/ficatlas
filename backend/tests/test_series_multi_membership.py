"""A work may sit in several series only when the ARCHIVE put it there.

An AO3 author can file one work in several series, and that is kept. Every
other source is this index's guess at a grouping, and two guesses holding the
same work were one guess counted twice: measured 2026-10-05, 130,324 detector
memberships duplicated a stronger series, and 2,838 of 3,000 sampled
sequel/stated pairs had identical membership.
"""
import uuid

import pytest
from sqlalchemy import text

from series_detect import supersede_weaker


def _work(db, title):
    n = uuid.uuid4().hex
    return str(db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author)
        VALUES ('ao3', :sid, :url, :t, 'An Author') RETURNING id
    """), {"sid": n[:16], "url": f"https://example.test/{n}", "t": title}).scalar())


def _series(db, name, source, works, ao3_id=None):
    sid = str(db.execute(text("""
        INSERT INTO series (name, author, source, work_count, ao3_id)
        VALUES (:n, 'an author', :s, :c, :a) RETURNING id
    """), {"n": name, "s": source, "c": len(works), "a": ao3_id}).scalar())
    for i, w in enumerate(works, 1):
        db.execute(text("INSERT INTO series_works (series_id, story_id, position) "
                        "VALUES (:s, :w, :p)"), {"s": sid, "w": w, "p": i})
    return sid


def _members(db, sid):
    return {str(r[0]) for r in db.execute(text(
        "SELECT story_id FROM series_works WHERE series_id = :s"), {"s": sid})}


def _exists(db, sid):
    return db.execute(text("SELECT 1 FROM series WHERE id = :s"), {"s": sid}).scalar()


@pytest.mark.usefixtures("db")
def test_two_archive_series_may_share_a_work(db):
    a, b, c = (_work(db, t) for t in "abc")
    s1 = _series(db, "Main", "explicit", [a, b], ao3_id="1")
    s2 = _series(db, "Event", "explicit", [b, c], ao3_id="2")
    db.commit()
    supersede_weaker(db)
    assert _members(db, s1) == {a, b} and _members(db, s2) == {b, c}


def test_a_guess_gives_way_to_the_archive_per_work(db):
    a, b, c, d = (_work(db, t) for t in "abcd")
    real = _series(db, "Real", "explicit", [a, b], ao3_id="9")
    guess = _series(db, "Guess series", "inferred", [a, c, d])
    db.commit()
    supersede_weaker(db)
    assert _members(db, real) == {a, b}
    # The shared work leaves; the two the archive does not file stay grouped.
    assert _members(db, guess) == {c, d}
    assert db.execute(text("SELECT work_count FROM series WHERE id=:s"), {"s": guess}).scalar() == 2


def test_identical_guesses_collapse_to_the_stronger(db):
    a, b, c = (_work(db, t) for t in "abc")
    stated = _series(db, "X series", "stated", [a, b, c])
    sequel = _series(db, "X saga", "sequel", [a, b, c])
    db.commit()
    supersede_weaker(db)
    assert _members(db, stated) == {a, b, c}
    assert not _exists(db, sequel)


def test_equal_guesses_keep_exactly_one(db):
    a, b, c = (_work(db, t) for t in "abc")
    big = _series(db, "Big", "inferred", [a, b, c])
    small = _series(db, "Small", "inferred", [b, c])
    db.commit()
    supersede_weaker(db)
    assert _members(db, big) == {a, b, c}
    assert not _exists(db, small)
    # Every work ends in exactly one guessed series.
    dup = db.execute(text("""
        SELECT count(*) FROM (SELECT story_id FROM series_works
                               GROUP BY 1 HAVING count(*) > 1) x""")).scalar()
    assert dup == 0
