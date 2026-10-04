"""The FF.net Wayback parser keeps fandom, characters, pairings and genres.

Structure copied from a real archived story page (fanfiction.net/s/14514421,
captured 2025-10-19); text abbreviated. It used to return none of these, so
every work this route added to the index had no fandom.
"""
from ffnet_wayback import _breadcrumb_fandoms, parse_story_snapshot

PAGE = """<html><head><title>What is Given, What is Taken, a hobbit fanfic | FanFiction</title></head><body>
<div style='margin-bottom: 10px' class='lc-wrapper' id=pre_story_links><span class=lc-left><a class=xcontrast_txt href='/book/'>Books</a><span class='xcontrast_txt icon-chevron-right xicon-section-arrow'></span><a class=xcontrast_txt href="/book/Hobbit/">Hobbit</a> </span> </div>
<div id=profile_top><b class='xcontrast_txt'>What is Given, What is Taken</b>
<a class='xcontrast_txt' href='/u/123456/someone'>someone</a>
<div style='margin-top:2px' class='xcontrast_txt'>A summary of the story.</div>
<span class='xgray xcontrast_txt'>Rated: <a>Fiction T</a> - English - Hurt/Comfort/Family - Thorin, Dwalin, Balin, D&#237;s - Words: 3,542 - Reviews: 2 - Follows: 1 - Published: 10/8/2025 - id: 14514421 </span>
</div>""" + ("<p>filler</p>" * 40) + "</body></html>"


def test_fandom_characters_and_genres_are_kept():
    d = parse_story_snapshot(PAGE, 14514421)
    assert d["fandoms"] == ["Hobbit"]
    assert d["characters"][:3] == ["Thorin", "Dwalin", "Balin"]
    assert d["genres"] == ["Hurt/Comfort", "Family"]
    assert d["tags"] == ["Hurt/Comfort", "Family"]
    assert d["published_at"] is not None


def test_a_crossover_breadcrumb_is_two_fandoms():
    html = ("<div id=pre_story_links><span><a href='/crossovers/'>Crossover</a>"
            "<a href=\"/Harry-Potter-and-Naruto-Crossovers/224/1402/\">Harry Potter + Naruto Crossover</a>"
            "</span></div>")
    assert _breadcrumb_fandoms(html) == ["Harry Potter", "Naruto"]


def test_a_section_alone_names_no_fandom():
    assert _breadcrumb_fandoms("<div id=pre_story_links><a href='/book/'>Books</a></div>") == []


def test_a_no_year_publication_date_uses_the_capture_year():
    from datetime import datetime, timezone
    page = PAGE.replace("Published: 10/8/2025", "Published: 10/8")
    d = parse_story_snapshot(page, 14514421, captured=datetime(2019, 11, 2, tzinfo=timezone.utc))
    assert d["published_at"] == datetime(2019, 10, 8, tzinfo=timezone.utc)
    assert parse_story_snapshot(page, 14514421)["published_at"] is None


def test_persist_keeps_the_publication_date():
    import inspect
    from live_fetch import persist
    src = inspect.getsource(persist)
    assert "published_at=None," not in src
    assert '_as_datetime(d.get("published_at"))' in src
