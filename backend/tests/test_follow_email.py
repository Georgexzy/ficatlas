"""Follow-update mail: opt-in, one digest, each chapter announced once.

See follow_email.py. The failure modes worth a test are the ones that mail a
reader who did not ask, or mail them repeatedly about one chapter — either of
which is how somebody marks the sender as spam.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import follow_email


def _user(db, email="r@example.test"):
    return str(db.execute(text(
        "INSERT INTO users (username, password_hash, email) VALUES (:n, 'x', :e) RETURNING id"),
        {"n": "u" + uuid.uuid4().hex[:8], "e": email}).scalar())


def _story(db, chapters, updated):
    n = uuid.uuid4().hex
    return str(db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, chapter_count, updated_at)
        VALUES ('ao3', :sid, :url, 'A Work', 'Writer', :c, :u) RETURNING id
    """), {"sid": n[:16], "url": f"https://example.test/{n}", "c": chapters, "u": updated}).scalar())


def _follow(db, user, story, seen, seen_updated):
    db.execute(text("""INSERT INTO follows (user_id, story_id, seen_chapters, seen_updated)
                       VALUES (:u, :s, :c, :t)"""),
               {"u": user, "s": story, "c": seen, "t": seen_updated})


def _opt_in(db, user, freq="daily"):
    db.execute(text("INSERT INTO follow_email (user_id, frequency, token) VALUES (:u, :f, :t)"),
               {"u": user, "f": freq, "t": uuid.uuid4().hex})


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, body, headers, html=None):
        self.sent.append((to, subject, body, headers))
        self.html = html
        return True


def test_nobody_is_mailed_without_opting_in(db):
    u = _user(db)
    _follow(db, u, _story(db, 5, T0 + timedelta(days=2)), 3, T0)
    db.commit()
    box = Outbox()
    follow_email.run(db, send=box)
    assert box.sent == []


def test_one_digest_then_silence_until_another_chapter(db):
    u = _user(db)
    s1 = _story(db, 5, T0 + timedelta(days=2))
    s2 = _story(db, 9, T0 + timedelta(days=3))
    _follow(db, u, s1, 3, T0)
    _follow(db, u, s2, 8, T0)
    _opt_in(db, u, "hourly")
    db.commit()
    box = Outbox()
    assert follow_email.run(db, send=box)["sent"] == 1
    to, subject, body, headers = box.sent[0]
    assert "2 stories" in subject and f"/story/{s1}" in body and "2 new chapters" in body
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    # Due again, nothing new: no mail, though the reader never opened either.
    db.execute(text("UPDATE follow_email SET last_sent_at = now() - interval '2 hours'"))
    db.commit()
    assert follow_email.run(db, send=box)["sent"] == 0

    # A further chapter is announced, and only that work.
    db.execute(text("UPDATE stories SET chapter_count = 6, updated_at = :t WHERE id = :s"),
               {"s": s1, "t": T0 + timedelta(days=4)})
    db.commit()
    assert follow_email.run(db, send=box)["sent"] == 1
    assert "1 new chapter " in box.sent[-1][2] and f"/story/{s2}" not in box.sent[-1][2]


def test_a_count_repair_is_not_news(db):
    # Chapters rose but the archive's date did not move: a metadata correction.
    u = _user(db)
    _follow(db, u, _story(db, 12, T0), 2, T0)
    _opt_in(db, u)
    db.commit()
    box = Outbox()
    follow_email.run(db, send=box)
    assert box.sent == []


def test_a_second_chapter_the_same_day_is_still_news(db):
    """Two chapters in one day: the archive's date cannot move, so it must not decide.

    AO3 dates a work by DAY. lonibal posted "Mental Gymnastics" (54) and
    "Intimate Relations" (55) both on 2026-10-14; the digest announced the
    first, the reader marked it seen, and the second was held back until the
    author posted again — because `updated_at > seen_updated` can never be
    true within one day. A re-read of the work page that found more chapters
    stamps chapters_bumped_at, and that is enough on its own.
    """
    day = datetime(2026, 10, 14, tzinfo=timezone.utc)
    u = _user(db)
    s = _story(db, 55, day)
    # Both chapters dated the same day, and both seen as of the first.
    _follow(db, u, s, 54, day)
    db.execute(text("UPDATE stories SET chapters_bumped_at = :b WHERE id = :s"),
               {"s": s, "b": day + timedelta(hours=18)})
    _opt_in(db, u, "hourly")
    db.commit()

    box = Outbox()
    assert follow_email.run(db, send=box)["sent"] == 1
    assert "1 new chapter " in box.sent[0][2]

    # And only once: the second digest has nothing left to say.
    db.execute(text("UPDATE follow_email SET last_sent_at = now() - interval '2 hours'"))
    db.commit()
    assert follow_email.run(db, send=box)["sent"] == 0


def test_a_corrected_count_years_late_is_not_news(db):
    """The bump alone is not licence to mail — the work's own date must agree.

    The title-repair backfill reads the WORK PAGE too, and there a dump
    reporting 12 for a work that really has 40 is a correction. The count
    rises and chapters_bumped_at is stamped, but the work is two years old and
    nothing was posted; mailing "28 new chapters!" about a fixed number is the
    exact case the date test was written to refuse.
    """
    u = _user(db)
    s = _story(db, 40, T0)                    # dated September 2026
    # The reader is already caught up to that date, so the ordinary date test
    # is false and the bump clause is the only thing that could mail them.
    _follow(db, u, s, 2, T0)
    db.execute(text("UPDATE stories SET chapters_bumped_at = :b WHERE id = :s"),
               {"s": s, "b": T0 + timedelta(days=40)})   # counted now
    _opt_in(db, u)
    db.commit()
    box = Outbox()
    follow_email.run(db, send=box)
    assert box.sent == []


def test_apply_work_stamps_the_bump_only_when_the_count_grows(db):
    """The stamp is the evidence, so it is set exactly where the count is."""
    from ao3_title_repair import apply_work
    from models.story import Story

    story = Story(site="ao3", site_id="1", url="https://example.test/1",
                  title="A Work", author="Writer", chapter_count=54,
                  updated_at=datetime(2026, 10, 14, tzinfo=timezone.utc))
    db.add(story)
    db.commit()
    base = {"title": "A Work", "chapter_count": 54,
            "updated_at": datetime(2026, 10, 14, tzinfo=timezone.utc)}

    assert apply_work(story, dict(base)) == []
    assert story.chapters_bumped_at is None

    grown = dict(base, chapter_count=55, word_count=1000)
    assert "chapter_count" in apply_work(story, grown)
    assert story.chapters_bumped_at is not None

    # An unchanged re-read leaves the original stamp alone.
    stamp = story.chapters_bumped_at
    assert apply_work(story, dict(grown)) == []
    assert story.chapters_bumped_at == stamp


def test_daily_means_at_most_once_a_day(db):
    u = _user(db)
    _follow(db, u, _story(db, 5, T0 + timedelta(days=2)), 3, T0)
    _opt_in(db, u, "daily")
    db.execute(text("UPDATE follow_email SET last_sent_at = now() - interval '3 hours'"))
    db.commit()
    box = Outbox()
    follow_email.run(db, send=box)
    assert box.sent == []


def test_no_address_no_mail(db):
    u = _user(db, email=None)
    _follow(db, u, _story(db, 5, T0 + timedelta(days=2)), 3, T0)
    _opt_in(db, u)
    db.commit()
    box = Outbox()
    follow_email.run(db, send=box)
    assert box.sent == []
