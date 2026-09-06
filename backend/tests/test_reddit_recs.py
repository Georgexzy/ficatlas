"""Community recommendation as a signal the archives cannot supply.

"Most popular" sorts by `popularity`, a percentile blend of kudos, bookmarks,
comments and hits. That is READERSHIP. The works a community presses on
newcomers year after year are often older, longer, plot-driven and on
FanFiction.net — which is exactly where this index has the least engagement
data. Measured against the r/HPFanfiction most-linked list (1,462 works,
2012-2023):

                recommended   in index   HAS a popularity score
    FF.net            1,131        655                      565
    AO3                 331        303                       61

836 of the most-recommended Harry Potter fanfics of the decade could not appear
in "Most popular" at any position, because they have no engagement figure and
therefore no score. Not a ranking bug — missing data, which no reweighting
fixes. A reference count is the missing measurement.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reddit_recs_import as rr


HEADER = (",TOTAL,,,\n"
          "RANK,REFS,Title,URL,Author\n")


def test_parses_ffnet_and_ao3_ids_from_the_url():
    csv = HEADER + (
        '1,1376,Pureblood Pretense,https://www.fanfiction.net/s/7613196/1/,x\n'
        '2,900,Some AO3 Work,https://archiveofourown.org/works/12345,y\n')
    assert rr.parse(csv) == [("ffnet", "7613196", 1376), ("ao3", "12345", 900)]


def test_matching_is_by_archive_id_never_by_title():
    """Titles collide constantly in fanfiction — this index holds five works
    called "Manacled" — and a wrong match would attach a decade of somebody
    else's reputation to the wrong story."""
    csv = HEADER + '1,500,Manacled,https://www.fanfiction.net/s/999/1/,x\n'
    assert rr.parse(csv) == [("ffnet", "999", 500)]


def test_a_row_with_no_archive_url_is_skipped():
    csv = HEADER + '1,500,Somewhere Else,https://example.com/thing,x\n'
    assert rr.parse(csv) == []


def test_thousands_separators_in_the_count():
    csv = HEADER + '1,"1,376",T,https://www.fanfiction.net/s/1/1/,x\n'
    assert rr.parse(csv)[0][2] == 1376


def test_one_or_two_mentions_is_not_a_recommendation():
    """A decade-long list: anything anybody actually presses on newcomers
    clears the floor easily, and below it the count is noise."""
    csv = HEADER + (
        '1,2,Barely mentioned,https://www.fanfiction.net/s/1/1/,x\n'
        '2,3,Recommended,https://www.fanfiction.net/s/2/1/,x\n')
    assert [e[1] for e in rr.parse(csv)] == ["2"]


def test_a_changed_sheet_layout_yields_nothing_rather_than_nonsense():
    """The importer writes to 20M rows of live data. If the columns move it
    must import zero works, not import the wrong column as a reference count."""
    assert rr.parse(",,,\nSOMETHING,ELSE,ENTIRELY,NOPE\n1,2,3,4\n") == []


def test_an_empty_sheet_is_not_an_error():
    assert rr.parse("") == []


def test_the_bonus_is_weaker_than_naming_the_ship_and_stronger_than_a_tag():
    """A recommendation is a stronger claim than a trope tag matching and a
    weaker one than the reader naming the pairing outright."""
    from api.search import RECS_BONUS
    assert 1.0 < RECS_BONUS < 2.5


def test_the_bonus_can_be_turned_off_without_a_deploy():
    """The list covers ONE fandom. If it ever distorts a search it has to come
    down without a deploy — the same reason SEARCH_TROPE_TAGS has a switch."""
    import importlib
    import api.search as search
    os.environ["SEARCH_RECS_BONUS"] = "0"
    try:
        importlib.reload(search)
        assert search.RECS_BONUS == 0.0
    finally:
        del os.environ["SEARCH_RECS_BONUS"]
        importlib.reload(search)
