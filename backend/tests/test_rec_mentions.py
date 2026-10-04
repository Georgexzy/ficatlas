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


# ── the threshold for "recommended lately" ──────────────────────────────────

def test_the_bar_rises_with_the_fandoms_activity():
    from api.hubs import min_people_for
    assert min_people_for(0) == 2 and min_people_for(29) == 2
    assert min_people_for(30) == 3 and min_people_for(99) == 3
    assert min_people_for(299) == 4 and min_people_for(5000) == 5


def test_one_persons_mention_never_makes_the_block(db):
    """Measured: all 54 works in the 30-day window had exactly one recommender,
    and the block showed them as what the fandom was recommending."""
    from api.hubs import _recommended
    for n in range(5):
        _mention(db, _story(db, f"Solo {n}"), f"reader{n}", days_ago=2)
    db.commit()
    works, days = _recommended(db, [FANDOM])
    assert works == [] and days == 0


def test_agreement_qualifies_and_a_quiet_fandom_gets_the_wider_window(db):
    from api.hubs import _recommended
    agreed = [_story(db, f"Agreed {n}") for n in range(3)]
    for sid in agreed:
        # Two people each, but 60 days ago: outside 30, inside 90.
        _mention(db, sid, "alice", days_ago=60)
        _mention(db, sid, "bob", days_ago=61)
    _mention(db, _story(db, "Only once"), "carol", days_ago=1)
    db.commit()
    works, days = _recommended(db, [FANDOM])
    assert days == 90
    assert sorted(w.title for w in works) == ["Agreed 0", "Agreed 1", "Agreed 2"]
    assert all(w.people == 2 for w in works)
