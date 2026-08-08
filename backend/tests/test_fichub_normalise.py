"""Unit tests for fichub_meta.normalise — metadata normalization.

This is the single choke point that turns a raw FicHub payload into the story
shape the index persists, so a mapping or parsing regression here corrupts every
import and every enrichment row. These tests pin the important conversions.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fichub_meta import normalise


def test_happy_path_full_payload():
    payload = {
        "source": "https://www.fanfiction.net/s/123/1/Title",
        "title": "  A Real Title  ",
        "author": "Some Author",
        "description": "<p>Hello <b>world</b></p>",
        "words": "12,345",
        "chapters": "7",
        "created": "2020-01-01T00:00:00Z",
        "updated": "2021-06-01T00:00:00Z",
        "rawExtendedMeta": {
            "status": "Completed",
            "rated": "M",
            "language": "English",
            "characters": "Hermione G., Draco M.",
            "genres": "Romance/Drama",
            "favorites": "99",
            "follows": "50",
            "reviews": "12",
            "raw_fandom": "Harry Potter & Draco Malfoy",
            "crossover": "true",
            "published": "2020-01-01",
            "updated_ts": "2021-06-01",
        },
    }
    out = normalise(payload)
    assert out["title"] == "A Real Title"
    assert out["summary"] == "Hello world"
    assert out["word_count"] == 12345
    assert out["chapter_count"] == 7
    assert out["rating"] == "mature"
    assert out["status"] == "complete"
    assert out["genres"] == ["Romance", "Drama"]
    assert out["characters"] == ["Hermione G.", "Draco M."]
    assert out["fandoms"] == ["Harry Potter", "Draco Malfoy"]
    assert out["kudos"] == 99
    assert out["bookmarks"] == 50
    assert out["comments"] == 12
    assert out["is_crossover"] is True


def test_summary_html_is_stripped_to_plain_text():
    out = normalise({"source": "u", "title": "T",
                     "description": "<p>Line one</p><p>Line &amp; two</p>"})
    assert "<" not in out["summary"]
    assert "Line" in out["summary"]


def test_rating_mapping():
    for raw, want in [("K", "general"), ("K+", "general"), ("T", "teen"),
                      ("M", "mature"), ("MA", "explicit"),
                      ("General Audiences", "general"),
                      ("Teen And Up Audiences", "teen"),
                      ("Not Rated", "not_rated")]:
        out = normalise({"source": "u", "title": "T",
                         "rawExtendedMeta": {"rated": raw}})
        assert out["rating"] == want, f"{raw!r} -> {out['rating']!r}"


def test_status_inference_only_from_extended_block():
    # FicHub's top-level status is unreliable for AO3; only the extended block
    # (FFN) is trusted.
    out = normalise({"source": "u", "title": "T",
                     "status": "ongoing", "rawExtendedMeta": {"status": "Completed"}})
    assert out["status"] == "complete"
    out2 = normalise({"source": "u", "title": "T", "status": "Complete"})
    assert out2["status"] is None


def test_word_count_commas_and_missing():
    assert normalise({"source": "u", "title": "T",
                      "words": "1,000,000"})["word_count"] == 1000000
    assert normalise({"source": "u", "title": "T"})["word_count"] is None


def test_genres_split_and_lower_limits():
    out = normalise({"source": "u", "title": "T",
                     "rawExtendedMeta": {"genres": "Romance/Drama/Humor"}})
    assert out["genres"] == ["Romance", "Drama", "Humor"]


def test_dates_accepted_as_unix_or_iso():
    out = normalise({"source": "u", "title": "T",
                     "rawExtendedMeta": {"published": "1577836800"}})
    assert isinstance(out["published_at"], datetime)
    assert out["published_at"].year == 2020


def test_crossover_defaults_false():
    assert normalise({"source": "u", "title": "T"})["is_crossover"] is False
