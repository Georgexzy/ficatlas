"""Followed works are refreshed on purpose, not by luck. See follow_refresh.py."""
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import follow_refresh
from ao3_title_repair import apply_work
from models.story import Story


SECTIONS = [["/book/Harry-Potter/", 852000], ["/game/Pok%C3%A9mon/", 107000],
            ["/anime/Fairy-Tail/", 50000]]


def test_fandom_names_find_their_listing():
    assert follow_refresh.section_for("Harry Potter", SECTIONS) == "/book/Harry-Potter/"
    assert follow_refresh.section_for("Pokémon", SECTIONS) == "/game/Pok%C3%A9mon/"
    assert follow_refresh.section_for("Fairy Tail", SECTIONS) == "/anime/Fairy-Tail/"
    assert follow_refresh.section_for("Naruto", SECTIONS) is None


def test_new_chapters_bring_their_words():
    s = Story(title="T", chapter_count=12, word_count=40000)
    changed = apply_work(s, {"chapter_count": 13, "word_count": 43500})
    assert s.chapter_count == 13 and s.word_count == 43500
    assert {"chapter_count", "word_count"} <= set(changed)


def test_a_bigger_count_without_a_new_chapter_is_left_alone():
    # The dump stays the authority unless the work visibly grew.
    s = Story(title="T", chapter_count=12, word_count=40000)
    apply_work(s, {"chapter_count": 12, "word_count": 41000})
    assert s.word_count == 40000


def _story(db, site="ao3", status="in_progress", crawled=None, fandoms=None):
    n = uuid.uuid4().hex
    return str(db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, status, crawled_at, fandoms)
        VALUES (:site, :sid, :url, 'W', :st, :c, :f) RETURNING id
    """), {"site": site, "sid": str(int(n[:8], 16)), "url": f"https://example.test/{n}",
           "st": status, "c": crawled, "f": fandoms}).scalar())


def _follow(db, story):
    u = db.execute(text("INSERT INTO users (username, password_hash) VALUES (:n,'x') RETURNING id"),
                   {"n": "u" + uuid.uuid4().hex[:8]}).scalar()
    db.execute(text("INSERT INTO follows (user_id, story_id) VALUES (:u, :s)"), {"u": u, "s": story})


def test_only_followed_unfinished_stale_ao3_works_are_due(db):
    now = datetime.now(timezone.utc)
    stale = _story(db, crawled=now - timedelta(hours=5))
    fresh = _story(db, crawled=now - timedelta(minutes=30))
    done = _story(db, status="complete", crawled=now - timedelta(days=5))
    unfollowed = _story(db, crawled=now - timedelta(days=5))
    for s in (stale, fresh, done):
        _follow(db, s)
    db.commit()
    due = {str(r[0]) for r in follow_refresh.due_ao3(db, hours=3)}
    assert due == {stale}
    assert unfollowed not in due


def test_followed_ffnet_fandoms_are_published(db):
    db.execute(text("INSERT INTO app_settings (key, value) VALUES ('ffnet_sections', :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
               {"v": json.dumps({"at": 0, "sections": SECTIONS})})
    _follow(db, _story(db, site="ffnet", fandoms=["Fairy Tail"]))
    db.commit()
    assert follow_refresh.followed_ffnet_sections(db) == ["/anime/Fairy-Tail/"]
