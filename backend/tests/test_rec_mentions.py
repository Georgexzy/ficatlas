"""What "most recommended lately" counts, and what it refuses to count.

Both rules here were chosen against a specific way of being wrong, and neither
is visible from the outside once it regresses — a block that silently counts
mentions instead of people looks exactly like a block that is working.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db")

FANDOM = "Harry Potter - J. K. Rowling"


def _story(db, title="A Work"):
    sid = uuid.uuid4()
    db.execute(text("""
        INSERT INTO stories (id, site, site_id, url, title, author, fandoms,
                             gate_underage, gate_adult)
        VALUES (:i, 'ao3', :sid, :u, :t, 'Somebody', CAST(:f AS text[]), false, false)
    """), {"i": str(sid), "sid": str(uuid.uuid4())[:12], "t": title,
           "u": f"https://archiveofourown.org/works/{sid}", "f": [FANDOM]})
    return str(sid)


def _mention(db, story_id, who, days_ago, site_id="x"):
    db.execute(text("""
        INSERT INTO rec_mentions (source, community, thread_id, comment_id,
                                  recommender, mentioned_at, story_id, site, site_id)
        VALUES ('reddit','hpfanfiction','t1', :cid, :who, :at, :sid, 'ao3', :site_id)
    """), {"cid": str(uuid.uuid4()), "who": who, "sid": story_id,
           "at": datetime.now(timezone.utc) - timedelta(days=days_ago),
           "site_id": site_id})


def _ranked(db, days=30):
    return db.execute(text("""
        SELECT s.id, count(DISTINCT m.recommender) AS people
          FROM rec_mentions m JOIN stories s ON s.id = m.story_id
         WHERE m.mentioned_at > now() - make_interval(days => :d)
           AND s.fandoms && CAST(:f AS text[])
         GROUP BY s.id ORDER BY people DESC
    """), {"d": days, "f": [FANDOM]}).fetchall()


def test_counts_people_not_mentions(db):
    """One reader naming a favourite every fortnight is ONE recommendation.

    Counting mentions would let the most talkative person in a subreddit decide
    what a fandom is reading — the same mistake as reading `opens` instead of
    `people` in the traffic panel's route report.
    """
    loud = _story(db, "Named Four Times By One Person")
    broad = _story(db, "Named Once By Three People")
    for i in range(4):
        _mention(db, loud, "/u/enthusiast", days_ago=i, site_id=f"loud{i}")
    for who in ("/u/a", "/u/b", "/u/c"):
        _mention(db, broad, who, days_ago=2, site_id="broad")
    db.commit()

    rows = {str(r[0]): r[1] for r in _ranked(db)}
    assert rows[loud] == 1, "four mentions by one person is one recommendation"
    assert rows[broad] == 3
    # And the ordering that follows from it.
    assert str(_ranked(db)[0][0]) == broad


def test_the_window_excludes_older_recommendations(db):
    """The point of the table is LATELY. An all-time count is what the existing
    `reddit_recs` / `community_recs` markers already provide."""
    s = _story(db, "Recommended Long Ago")
    _mention(db, s, "/u/a", days_ago=200, site_id="old")
    db.commit()
    assert _ranked(db, days=30) == []
    assert len(_ranked(db, days=365)) == 1


def test_an_unmatched_link_is_kept_as_a_crawl_target(db):
    """A link to a work this index does not hold is the most useful thing a
    crawler could be told about — it is what a fandom is reading right now."""
    db.execute(text("""
        INSERT INTO rec_mentions (source, community, thread_id, comment_id,
                                  recommender, mentioned_at, story_id, site, site_id)
        VALUES ('reddit','hpfanfiction','t1','c-unmatched','/u/a', now(),
                NULL, 'ao3', '999999')
    """))
    db.commit()
    n = db.execute(text(
        "SELECT count(*) FROM rec_mentions WHERE story_id IS NULL")).scalar_one()
    assert n == 1
    # It must never reach a hub, which joins to stories.
    assert _ranked(db) == []


def test_one_comment_naming_a_work_twice_is_stored_once(db):
    """The uniqueness constraint is (comment_id, site, site_id): somebody who
    links a work and then links it again in the same comment has recommended it
    once."""
    s = _story(db)
    for _ in range(2):
        db.execute(text("""
            INSERT INTO rec_mentions (source, community, thread_id, comment_id,
                                      recommender, mentioned_at, story_id, site, site_id)
            VALUES ('reddit','hpfanfiction','t1','same-comment','/u/a', now(),
                    :sid, 'ao3', 'dup')
            ON CONFLICT (comment_id, site, site_id) DO NOTHING
        """), {"sid": s})
    db.commit()
    assert db.execute(text(
        "SELECT count(*) FROM rec_mentions WHERE comment_id='same-comment'")).scalar_one() == 1
