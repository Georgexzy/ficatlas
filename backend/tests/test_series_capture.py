"""Every AO3 page we fetch already states the series. The work-page readers
were the ones that did not record it.

Three places fetch an AO3 work page — the title-repair pass, the stale-WIP
refresh (which the follow refresh shares) and ao3_stub_enrich's drain of the
4.1M bulk-indexed stubs — and all three parsed it for chapters, words and
tags and dropped the `<dd class="series">` block on the floor. Only the
listing blurb (`live_fetch.persist`) and the two series-specific jobs ever
recorded one.

Which is how a work ends up filed under a series its author never wrote. What
a reader reported: `The Evans Boy` by lonibal showed ONE series, an invented
one-work grouping called "Evans Boy Deathly Hallows stuff series" that a
detector had read off a SUMMARY line, and not the archive's own "The Evans
Boy" — which the page states plainly as `Part 1 of The Evans Boy`, and which
runs to five works. AO3 said one series; we showed a different one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

import ao3_stub_enrich
from ao3_title_repair import apply_work, parse_work_page
from live_fetch.persist import persist_live_results
from models.story import Story
from series_detect import supersede_weaker

# Shaped on the real work page, including AO3's own habit of writing the part
# number inside a <strong> and the previous/next links inside the same block
# (they are why ao3_series splits the block from the positions).
WORK_PAGE = """
<html><body><div id="main">
  <h2 class="title heading"><a href="/works/51358966">The Evans Boy</a></h2>
  <p class="byline">by <a rel="author" href="/users/lonibal">lonibal</a></p>
  <div class="summary module">
    <blockquote class="userstuff"><p>A boy who was almost nobody.</p></blockquote>
  </div>
  <dl class="work meta group">
    <dt class="series tags">Series:</dt>
    <dd class="series"><span class="series"><span class="position">Part
      <strong>1</strong> of <a href="/series/4228465">The Evans Boy</a></span>
      <span class="divider"> </span><a class="next" href="/works/57085603">Next Work</a></span></dd>
    <dt class="language">Language:</dt><dd class="language">English</dd>
    <dt class="words">Words:</dt><dd class="words">670,230</dd>
    <dt class="chapters">Chapters:</dt><dd class="chapters">173/173</dd>
    <dt class="kudos">Kudos:</dt><dd class="kudos">16,565</dd>
  </dl>
</div></body></html>
"""

# What the listing blurb (and the series-page listing) hands to persist.
BLURB_SERIES = [{"position": 1, "ao3_id": "4228465", "name": "The Evans Boy"}]


def _members(db, title="The Evans Boy"):
    return db.execute(text("""
        SELECT se.name, se.source, sw.position, sw.role
          FROM series_works sw JOIN series se ON se.id = sw.series_id
          JOIN stories s ON s.id = sw.story_id
         WHERE s.title = :t
    """), {"t": title}).fetchall()


# ── The block is read ────────────────────────────────────────────────────────

def test_the_refresh_pass_reads_the_series_block():
    """The pass that fetches a work page for its chapters has the series too."""
    data = parse_work_page(WORK_PAGE)
    assert data is not None
    assert data["series"] == [{"position": 1, "ao3_id": "4228465",
                               "name": "The Evans Boy"}]


def test_the_stub_drain_reads_it_too():
    """ao3_stub_enrich has its own parser, and its rows are the ones with no
    series field to have inherited from the bulk dumps."""
    assert ao3_stub_enrich.parse_work(WORK_PAGE)["series"] == BLURB_SERIES


def test_a_page_with_no_series_says_so_rather_than_raising():
    html = "<html><h2 class=\"title heading\">Bare</h2></html>"
    assert parse_work_page(html)["series"] == []
    assert ao3_stub_enrich.parse_work(html)["series"] == []


# ── …and recorded ────────────────────────────────────────────────────────────

def _insert(db, title="The Evans Boy", author="lonibal"):
    return str(db.execute(text("""
        INSERT INTO stories (site, site_id, url, title, author, status)
        VALUES ('ao3', :sid, :url, :t, :a, 'complete') RETURNING id
    """), {"sid": "51358966", "url": "https://archiveofourown.org/works/51358966",
           "t": title, "a": author}).scalar())


def test_reading_a_work_page_files_the_work(db):
    sid = _insert(db)
    story = db.query(Story).filter(Story.id == sid).first()

    changed = apply_work(story, parse_work_page(WORK_PAGE))
    db.commit()

    assert "series" in changed
    assert _members(db) == [("The Evans Boy", "explicit", 1, "main")]
    assert db.query(Story).filter(Story.id == sid).first().has_series


def test_reading_it_again_learns_nothing(db):
    """`changed` is what the caller counts, so a re-read that merely repeats
    the series must not report one — otherwise every refresh pass claims to
    have found a series it already had."""
    sid = _insert(db)
    story = db.query(Story).filter(Story.id == sid).first()
    apply_work(story, parse_work_page(WORK_PAGE))
    db.commit()

    again = apply_work(story, parse_work_page(WORK_PAGE))
    db.commit()
    assert "series" not in again
    assert len(_members(db)) == 1


def test_a_work_page_read_without_a_session_does_not_raise(db):
    """The unit tests above pass a detached Story; so can a caller. The series
    is dropped rather than raising out of a refresh that otherwise succeeded."""
    detached = Story(title="T", chapter_count=1)
    changed = apply_work(detached, {"series": BLURB_SERIES})
    assert "series" not in changed


# ── The archive's word ends the guessing, on the next supersede pass ─────────

def test_the_archive_takes_the_work_out_of_our_guess(db):
    """The shape of the reported bug, end to end.

    A detector groups the work under a name it invented; the work page then
    tells us where the author actually put it. The guess is expected to lose —
    supersede_weaker does that for every work every three hours — and to be
    dropped, not left behind as a one-work series nobody asked for.
    """
    sid = _insert(db)
    db.execute(text("""
        INSERT INTO series (name, author, site, source, confidence, work_count)
        VALUES ('Evans Boy Deathly Hallows stuff series', 'lonibal', 'ao3',
                'inferred', 1.0, 1)
    """))
    guessed_id = str(db.execute(text("SELECT id FROM series")).scalar())
    db.execute(text("""
        INSERT INTO series_works (series_id, story_id, role)
        VALUES (:s, :w, 'side')
    """), {"s": guessed_id, "w": sid})

    story = db.query(Story).filter(Story.id == sid).first()
    apply_work(story, parse_work_page(WORK_PAGE))
    db.commit()
    assert {m[0] for m in _members(db)} == {
        "Evans Boy Deathly Hallows stuff series", "The Evans Boy"}

    supersede_weaker(db)

    assert _members(db) == [("The Evans Boy", "explicit", 1, "main")]
    assert db.execute(text("SELECT count(*) FROM series")).scalar() == 1


# ── The listing blurb, for a work we already hold ────────────────────────────

def _blob(**over):
    d = {
        "url": "https://archiveofourown.org/works/111111",
        "title": "My Story", "author": "Jane Doe", "summary": "A summary.",
        "language": "English", "rating": "T", "status": "in_progress",
        "word_count": 1000, "chapter_count": 2, "kudos": 10, "hits": 100,
        "bookmarks": 1, "comments": 2, "fandoms": ["Harry Potter"],
        "characters": [], "relationships": [], "tags": [], "warnings": [],
        "categories": [], "genres": [], "updated_at": "2024-01-01T00:00:00Z",
    }
    d.update(over)
    return d


def test_a_blurb_can_give_a_held_work_its_series(db):
    """AO3's metadata dump carries no series field, so a row imported from it
    has none until a blurb or a work page says otherwise."""
    assert persist_live_results(db, [_blob()]) == 1
    assert _members(db, "My Story") == []

    stats = {}
    persist_live_results(db, [_blob(series=BLURB_SERIES)], stats)

    assert stats["enriched"] == 1, "a series we did not hold is an enrichment"
    assert _members(db, "My Story") == [("The Evans Boy", "explicit", 1, "main")]


def test_rereading_the_same_blurb_learns_nothing(db):
    """Same reason as the work-page re-read: `enriched` has to keep meaning
    "we learned something", or it reports a fetch instead."""
    persist_live_results(db, [_blob()])
    persist_live_results(db, [_blob(series=BLURB_SERIES)])

    stats = {}
    persist_live_results(db, [_blob(series=BLURB_SERIES)], stats)

    assert stats["enriched"] == 0 and stats["already_indexed"] == 1
    assert len(_members(db, "My Story")) == 1
