"""Series built by the detector must not hold two works on one number.

Measured 2026-10-04: 761 detector-built series did, by two routes — a title
the position patterns could not read ("Part 10a", "Day 10 Body") formed a
second group under the same name, and the upsert by name merged it in with its
own numbering; and an event run twice ("Ghostober" 2023 and 2024) repeats every
number by construction.
"""
from datetime import datetime, timezone

from series_detect import merge_same_named, resolve_positions
from series_titles import group_by_structure, split_position


def _w(i, title, pos=None):
    return {"id": str(i), "title": title, "summary": "", "numeric_id": i,
            "published_at": None, "pos": pos}


def test_god_help_me_is_one_group_in_order():
    titles = ["God Help Me, Part 1", "God Help Me, Part 2", "God Help Me, Part 3",
              "God Help Me, Part 10a", "God Help Me, Part 10b",
              "God Help Me, Part 11--All The Way to The End"]
    groups = group_by_structure([_w(i, t) for i, t in enumerate(titles, 1)])
    assert len(groups) == 1
    assert [p for _, p in groups[0]["members"]][:3] == [1, 2, 3]


def test_ordinary_titles_are_not_numbered():
    for t in ["Seven Days", "A Day in the Life", "Part of Me", "Book of Shadows", "21 Questions"]:
        assert split_position(t)[1] is None, t


def test_sub_parts_keep_the_authors_order():
    ms = [_w(1, "1", 1), _w(2, "2", 2), _w(10, "10a", 10), _w(11, "10b", 10), _w(12, "11", 11)]
    assert resolve_positions(ms)
    assert [m["title"] for m in sorted(ms, key=lambda m: m["pos"])] == ["1", "2", "10a", "10b", "11"]
    assert len({m["pos"] for m in ms}) == 5


def test_a_repeated_cycle_is_ordered_by_publication():
    # Two Octobers of Day 1-3, ids increasing with publication.
    ms = [_w(i, f"Day {d} ({y})", d) for i, (y, d) in enumerate(
        [(2023, 1), (2023, 2), (2023, 3), (2024, 1), (2024, 2), (2024, 3)], 1)]
    assert resolve_positions(ms)
    assert [m["title"] for m in sorted(ms, key=lambda m: m["pos"])] == [
        "Day 1 (2023)", "Day 2 (2023)", "Day 3 (2023)", "Day 1 (2024)", "Day 2 (2024)", "Day 3 (2024)"]


def test_clean_numbers_and_side_stories_are_untouched():
    ms = [_w(1, "a", 1), _w(2, "b", 3), _w(3, "side", None)]
    assert not resolve_positions(ms)
    assert [m["pos"] for m in ms] == [1, 3, None]


def test_same_named_groups_are_numbered_as_one():
    g = merge_same_named([("Ghostober", [_w(1, "x")], 30.0, "inferred"),
                          ("ghostober", [_w(2, "y"), _w(1, "x")], 28.0, "stated")])
    assert len(g) == 1 and [m["id"] for m in g[0][1]] == ["1", "2"]
    assert g[0][3] == "stated"
