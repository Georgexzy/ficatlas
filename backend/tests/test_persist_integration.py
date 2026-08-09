"""Integration tests for the canonical ingest path.

persist_live_results is where every live fetch funnels new rows into the index.
Its cross-post merge and _enrich_existing are the highest-risk pieces: a silent
exception there used to turn every merge into a duplicate insert, and a slip in
"forward-only" enrichment can downgrade a row we already hold. These tests pin
the behaviour against a real Postgres (a *_test database) so those failure modes
can't come back unnoticed.
"""
import sys
import os
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_fetch.persist import persist_live_results, _enrich_existing
from live_fetch.crosspost import find_crosspost_for, merge_group
from models.story import Story, SiteEnum, StatusEnum

AO3_URL = "https://archiveofourown.org/works/111111"
FFN_URL = "https://www.fanfiction.net/s/9999999/1/My-Story"


def _ao3_blob(**over):
    d = {
        "url": AO3_URL,
        "title": "My Story",
        "author": "Jane Doe",
        "summary": "A summary.",
        "language": "English",
        "rating": "T",
        "status": "in_progress",
        "word_count": 1000,
        "chapter_count": 2,
        "chapter_count_total": None,
        "kudos": 10,
        "hits": 100,
        "bookmarks": 1,
        "comments": 2,
        "fandoms": ["Harry Potter"],
        "characters": ["Hermione"],
        "relationships": [],
        "tags": [],
        "warnings": [],
        "categories": [],
        "genres": [],
        "updated_at": "2024-01-01T00:00:00Z",
    }
    d.update(over)
    return d


def _ffn_blob(**over):
    d = _ao3_blob()
    d.update({"url": FFN_URL, "title": "My Story", "author": "Jane Doe"})
    d.update(over)
    return d


def test_new_insert_saved(db):
    assert persist_live_results(db, [_ao3_blob()]) == 1
    row = db.query(Story).filter(Story.url == AO3_URL).first()
    assert row is not None
    assert row.site == SiteEnum.ao3
    assert row.site_id == "111111"
    assert row.title == "My Story"
    assert row.author == "Jane Doe"
    assert row.updated_at is not None and row.updated_at.tzinfo is not None


def test_same_url_is_deduped_not_duplicated(db):
    stats = {}
    persist_live_results(db, [_ao3_blob()], stats)
    assert stats["saved"] == 1

    stats2 = {}
    persist_live_results(db, [_ao3_blob()], stats2)
    assert stats2["saved"] == 0
    assert stats2["already_indexed"] == 1
    assert db.query(Story).filter(Story.url == AO3_URL).count() == 1


def test_optout_summary_not_saved(db):
    stats = {}
    blob = _ao3_blob(summary="Please do not repost this work on other websites.")
    assert persist_live_results(db, [blob], stats) == 0
    assert stats["already_indexed"] == 1
    assert db.query(Story).filter(Story.url == AO3_URL).count() == 0


def test_optout_deletes_existing_row(db):
    persist_live_results(db, [_ao3_blob()])
    assert db.query(Story).filter(Story.url == AO3_URL).count() == 1

    stats = {}
    blob = _ao3_blob(summary="Do not redistribute this work. Reposting is prohibited.")
    persist_live_results(db, [blob], stats)
    assert stats["already_indexed"] == 1
    assert db.query(Story).filter(Story.url == AO3_URL).count() == 0


def test_crosspost_merges_no_duplicate(db):
    # AO3 copy already indexed.
    assert persist_live_results(db, [_ao3_blob()]) == 1

    # Incoming FFN copy of the same work must merge, not insert.
    stats = {}
    assert persist_live_results(db, [_ffn_blob()], stats) == 0
    assert stats["cross_post_merged"] == 1
    assert db.query(Story).count() == 1

    row = db.query(Story).filter(Story.url == AO3_URL).first()
    assert row is not None
    assert FFN_URL in (row.cross_post_urls or [])


def test_crosspost_fresh_copy_wins(db):
    # Keep the AO3 copy stale, then feed a much newer FFN copy: the merge must
    # advance the canonical row's update time from the incoming blurb.
    persist_live_results(db, [_ao3_blob(updated_at="2020-01-01T00:00:00Z")])

    stats = {}
    persist_live_results(
        db, [_ffn_blob(updated_at="2024-06-01T00:00:00Z", word_count=5000,
                       status="complete")], stats)
    assert stats["cross_post_merged"] == 1

    row = db.query(Story).filter(Story.url == AO3_URL).first()
    assert row.word_count == 5000
    assert row.status == StatusEnum.complete


def test_enrich_fills_gaps_not_overwrites(db):
    # Insert a sparse row (no summary, low counts), then re-persist the same URL
    # with a richer blurb.
    stats = {}
    persist_live_results(db, [_ao3_blob(summary=None, kudos=10, word_count=1000)], stats)
    assert stats["saved"] == 1

    stats2 = {}
    persist_live_results(
        db, [_ao3_blob(summary="The real summary.", kudos=25, word_count=2000,
                       updated_at="2024-05-01T00:00:00Z")], stats2)
    assert stats2["enriched"] == 1

    row = db.query(Story).filter(Story.url == AO3_URL).first()
    assert row.summary == "The real summary."
    assert row.kudos == 25
    assert row.word_count == 2000


def test_enrich_does_not_downgrade(db):
    # A richer stored row must never be downgraded by a stale/short blurb.
    persist_live_results(db, [_ao3_blob(summary="Original full summary.",
                                        word_count=9000, kudos=100)])

    # Nothing changes here, so persist reports it as already-indexed (not
    # enriched) — the point is that the stored values are untouched.
    persist_live_results(
        db, [_ao3_blob(summary="", word_count=100, kudos=1,
                       updated_at="2020-01-01T00:00:00Z")])

    row = db.query(Story).filter(Story.url == AO3_URL).first()
    assert row.summary == "Original full summary."
    assert row.word_count == 9000
    assert row.kudos == 100


def test_find_crosspost_by_author_and_title(db):
    persist_live_results(db, [_ao3_blob()])
    found = find_crosspost_for(db, "My Story", "Jane Doe", exclude_url=FFN_URL)
    assert found is not None
    assert found.url == AO3_URL


def test_find_crosspost_placeholder_identity_returns_none(db):
    persist_live_results(db, [_ao3_blob(title="Unknown", author="Anonymous")])
    assert find_crosspost_for(db, "Unknown", "Anonymous") is None


def test_merge_group_unions_and_deletes(db):
    # Insert two copies of the same work directly (persist_live_results would
    # already merge them via find_crosspost_for, which is its own test above).
    a = Story(site=SiteEnum.ao3, site_id="111111", url=AO3_URL,
              title="My Story", author="Jane Doe", summary="A summary.",
              word_count=1000, chapter_count=1, kudos=10, hits=100,
              fandoms=["Harry Potter"], characters=["Hermione"],
              is_crossover=False)
    b = Story(site=SiteEnum.ffnet, site_id="9999999", url=FFN_URL,
              title="My Story", author="Jane Doe", summary="A summary.",
              word_count=2000, chapter_count=1, kudos=5, hits=50,
              fandoms=["Original Character"], characters=[],
              is_crossover=False)
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)

    canonical = merge_group(db, [a, b])
    db.commit()

    # The longer copy (b) wins as canonical; the other row is deleted.
    assert canonical.id == b.id
    assert canonical.url == FFN_URL
    assert canonical.fandoms is not None
    vals = [f.lower() for f in canonical.fandoms]
    assert "harry potter" in vals and "original character" in vals
    assert db.query(Story).filter(Story.url == FFN_URL).count() == 1
    assert db.query(Story).filter(Story.url == AO3_URL).count() == 0
    assert AO3_URL in (canonical.cross_post_urls or [])
