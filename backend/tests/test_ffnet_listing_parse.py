"""FF.net LISTING rows parse with the same parser as archived story pages.

Every line here is a real row from fanfiction.net/book/Harry-Potter/ (favs sort,
page 40), fetched 2026-10-04. A listing row carries the same metadata line as a
story page but in a different order — characters after the dates — and with
month-name dates, which is why the harvester's 246,861 rows a day never filled
a single character before this.
"""
from datetime import datetime, timezone

from ffnet_enrich import _ffn_date, parse_ffn_meta

ROWS = [
    ("Rated: K+ - English - Adventure - Chapters: 3 - Words: 14,977 - Reviews: 631 - "
     "Favs: 4,365 - Follows: 2,254 - Updated: Sep 22, 2013 - Published: Sep 8, 2013 - "
     "Harry P. - Complete"),
    ("Rated: M - English - Chapters: 78 - Words: 608,017 - Reviews: 915 - Favs: 4,365 - "
     "Follows: 4,962 - Updated: Aug 22 - Published: Jun 28, 2022 - "
     "Harry P., Hermione G., Fleur D., Daphne G."),
    ("Rated: M - English - Romance/Drama - Chapters: 30 - Words: 163,513 - Reviews: 2049 - "
     "Favs: 4,361 - Follows: 2,219 - Updated: Jul 1, 2014 - Published: Oct 17, 2012 - "
     "Hermione G., Tom R. Jr. - Complete"),
]


def test_characters_after_the_dates_are_read_on_a_listing_row():
    m = parse_ffn_meta(ROWS[0], listing=True)
    assert m["characters"] == ["Harry P."]
    assert m["genres"] == ["Adventure"]
    assert m["words"] == 14977 and m["favs"] == 4365 and m["chapters"] == 3
    assert m["complete"] is True
    assert m["published_at"] == datetime(2013, 9, 8, tzinfo=timezone.utc)


def test_no_genre_and_still_going():
    m = parse_ffn_meta(ROWS[1], listing=True)
    assert m["characters"] == ["Harry P.", "Hermione G.", "Fleur D.", "Daphne G."]
    assert m["genres"] == []
    assert m["complete"] is False          # FF.net prints nothing for a WIP


def test_a_suffix_in_a_name_survives_and_slashed_genres_split():
    m = parse_ffn_meta(ROWS[2], listing=True)
    assert m["characters"] == ["Hermione G.", "Tom R. Jr."]
    assert m["genres"] == ["Romance", "Drama"]


def test_story_page_ordering_is_unchanged_without_the_flag():
    # Without `listing`, a segment after the counts is still not a character —
    # the story-page rule the existing Wayback path depends on.
    assert parse_ffn_meta(ROWS[0])["characters"] == []


def test_pairings_in_brackets_on_a_listing_row():
    m = parse_ffn_meta("Rated: T - English - Romance - Words: 1,000 - Favs: 3 - "
                       "Published: Jan 2, 2010 - [Harry P., Draco M.] Ron W. - Complete",
                       listing=True)
    assert m["relationships"] == ["Harry P./Draco M."]
    assert m["characters"] == ["Harry P.", "Draco M.", "Ron W."]


def test_month_name_dates_and_the_missing_year():
    assert _ffn_date("Sep 22, 2013") == datetime(2013, 9, 22, tzinfo=timezone.utc)
    d = _ffn_date("Aug 22")
    now = datetime.now(timezone.utc)
    assert d is not None and d <= now and (now - d).days < 366
    # The numeric forms still work.
    assert _ffn_date("11/26/2012") == datetime(2012, 11, 26, tzinfo=timezone.utc)


def test_a_staged_listing_row_fills_the_work_it_names(db):
    """End to end on the test database: stage, apply, read back."""
    from sqlalchemy import text
    from models.story import Story, SiteEnum, StatusEnum
    import ffnet_listing

    db.execute(text("DELETE FROM ffnet_listing_meta"))
    s = Story(site=SiteEnum.ffnet, site_id="9431234", url="https://www.fanfiction.net/s/9431234/1/",
              title="Consequences of an Ingenious Mind", author="White Angel of Auralon",
              word_count=0, chapter_count=1, characters=[], relationships=[], genres=[],
              status=StatusEnum.unknown, is_crossover=False)
    db.add(s)
    db.commit()
    db.execute(text("INSERT INTO ffnet_listing_meta (site_id, line) VALUES (9431234, :a), (777, :b)"),
               {"a": ROWS[0], "b": ROWS[1]})
    db.commit()

    stats = ffnet_listing.apply_batch(100)
    # Both claimed and parsed; only the one we hold is written.
    assert stats["claimed"] == 2 and stats["parsed"] == 2
    assert stats["matched"] == 1 and stats["written"] == 1 and stats["with_characters"] == 1

    db.expire_all()
    got = db.query(Story).filter(Story.site_id == "9431234").one()
    assert got.characters == ["Harry P."]
    assert got.genres == ["Adventure"]
    assert got.word_count == 14977
    assert got.status == StatusEnum.complete
    assert got.kudos == 4365
    # Claimed rows are gone: each staged line is applied at most once.
    assert db.execute(text("SELECT count(*) FROM ffnet_listing_meta")).scalar() == 0
