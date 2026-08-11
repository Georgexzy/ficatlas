"""Reading FanFiction.net metadata out of an Internet Archive snapshot.

FF.net has blocked automated access since 2021 and every endpoint tested from
this host — story pages, listings, profiles, Atom, RSS, mobile — returns a
Cloudflare challenge. The Archive crawls it independently and their CDX API is
public, so the route is to read their copy rather than ask FF.net.

The risk in doing that is what a snapshot can contain. Wayback stores whatever
it got, and plenty of 200s are not story pages at all: Cloudflare challenges
captured mid-block, FF.net's own error pages, redirects. Anything that reaches
the database from here has to have been checked, because a half-parsed capture
would overwrite good metadata with nonsense.
"""
import pytest

from ffnet_wayback import (cdx_params, parse_story_snapshot, story_id_from_url)

# Trimmed from a real capture: https://web.archive.org/web/20260101152421id_/
# https://www.fanfiction.net/s/10000890/1/Compromise
REAL = """
<html><head><title>Compromise, a soul eater fanfic | FanFiction</title></head>
<body>
<b class='xcontrast_txt'>Compromise</b>
<a class='xcontrast_txt' href='/u/4394331/Lady-Eclipse'>Lady-Eclipse</a>
<div style='margin-top:2px' class='xcontrast_txt'>A compromise. That's what he
called it as he strapped her to the operating table.</div>
<span class='xgray xcontrast_txt'>Rated: <a href='https://www.fictionratings.com/'>Fiction  M</a>
 - English - Romance - [Marie M., Franken Stein] - Words: 2,576 - Reviews: 4
 - Favs: 10 - Follows: 10 - Published: 1/6/2014 - id: 10000890 </span>
</body></html>
"""

MULTI_CHAPTER = REAL.replace(
    "Words: 2,576", "Chapters: 12 - Words: 84,201").replace(
    "Published: 1/6/2014", "Updated: 3/2/2015 - Published: 1/6/2014 - Complete")


class TestParsing:
    def test_a_real_capture(self):
        d = parse_story_snapshot(REAL, 10000890)
        assert d is not None
        assert d["title"] == "Compromise"
        assert d["site"] == "ffnet"
        assert d["site_id"] == "10000890"
        assert d["word_count"] == 2576
        assert d["reviews"] == 4
        assert d["rating"] == "M"
        assert d["language"] == "English"

    def test_favourites_become_kudos(self):
        """FF.net favourites are the nearest thing it has to AO3 kudos, and kudos
        is the column everything else in this app ranks on."""
        assert parse_story_snapshot(REAL, 10000890)["kudos"] == 10

    def test_a_single_chapter_story(self):
        assert parse_story_snapshot(REAL, 10000890)["chapter_count"] == 1

    def test_chapters_updated_and_complete(self):
        d = parse_story_snapshot(MULTI_CHAPTER, 10000890)
        assert d["chapter_count"] == 12
        assert d["word_count"] == 84201
        assert d["status"] == "complete"
        assert d["updated_at"].year == 2015

    def test_dates(self):
        d = parse_story_snapshot(REAL, 10000890)
        assert (d["published_at"].year, d["published_at"].month) == (2014, 1)
        # With no Updated:, the published date stands in rather than a null that
        # would sort the work to the bottom of every date-ordered list.
        assert d["updated_at"] == d["published_at"]

    def test_summary_and_author(self):
        d = parse_story_snapshot(REAL, 10000890)
        assert d["summary"].startswith("A compromise.")
        assert "Eclipse" in d["author"]

    def test_the_canonical_url_is_ffnet_not_the_archive(self):
        """What we store has to send a reader to the work, not to our snapshot
        of it."""
        d = parse_story_snapshot(REAL, 10000890)
        assert d["url"] == "https://www.fanfiction.net/s/10000890/1/"
        assert "web.archive.org" not in d["url"]


class TestRefusals:
    """Wayback returns 200 for a great many things that are not story pages."""

    @pytest.mark.parametrize("html", [
        "", None, "<html></html>",
        "<html><head><title>Just a moment...</title></head><body>"
        "<div>Checking your browser</div></body></html>",
        "<html><body>Story Not Found</body></html>",
    ])
    def test_not_a_story_page(self, html):
        assert parse_story_snapshot(html, 123) is None

    def test_a_stats_line_without_a_title_is_refused(self):
        html = "<html><body><span>Rated: Fiction T - English - Words: 100</span></body></html>"
        assert parse_story_snapshot(html, 123) is None


class TestUrls:
    def test_story_id_from_a_first_chapter_url(self):
        assert story_id_from_url("https://www.fanfiction.net/s/10000890/1/Compromise") == 10000890
        assert story_id_from_url("http://fanfiction.net/s/123/1/") == 123

    def test_later_chapters_are_skipped(self):
        """Every chapter page repeats the same metadata block, so fetching them
        spends the archive's bandwidth to learn nothing."""
        assert story_id_from_url("https://www.fanfiction.net/s/10000890/7/Compromise") is None

    def test_non_story_urls(self):
        assert story_id_from_url("https://www.fanfiction.net/u/4394331/") is None
        assert story_id_from_url("") is None


class TestCdxQuery:
    def test_asks_only_for_successful_captures(self):
        p = cdx_params()
        assert p["filter"] == "statuscode:200"
        assert p["matchType"] == "prefix"

    def test_collapses_to_one_row_per_story(self):
        """A popular fic has hundreds of snapshots; we want the newest of each,
        not all of them."""
        assert cdx_params()["collapse"] == "urlkey"

    def test_resume_key_is_passed_through(self):
        assert cdx_params(resume="abc")["resumeKey"] == "abc"
