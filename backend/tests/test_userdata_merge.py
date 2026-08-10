"""Merging reading progress across devices.

Pure-function tests: `_merge_value` takes the client's blob and the server's and
returns what both should now hold. No database, no fixtures.

The case that matters is two devices reading the same story. Progress used to be
one number per story, so whole-entry last-write-wins was correct. It now carries
a position per chapter, and replacing the whole entry throws away chapters the
other device had read — which is the bug these tests exist to hold shut.
"""
from api.userdata import _merge_value


def _entry(chapter, at, positions=None, **extra):
    e = {"chapter": chapter, "at": at, "totalChapters": 20}
    if positions is not None:
        e["positions"] = positions
    e.update(extra)
    return e


def test_story_only_on_one_side_is_kept():
    client = {"s1": _entry(3, "2026-08-09T10:00:00")}
    server = {"s2": _entry(5, "2026-08-09T09:00:00")}
    out = _merge_value("progress", client, server)
    assert set(out) == {"s1", "s2"}


def test_newer_entry_wins_the_scalar_fields():
    """Which chapter you are on is genuinely last-write-wins."""
    client = {"s1": _entry(7, "2026-08-09T12:00:00")}
    server = {"s1": _entry(3, "2026-08-09T10:00:00")}
    assert _merge_value("progress", client, server)["s1"]["chapter"] == 7

    # ...in both directions, so the result does not depend on who is asking.
    client = {"s1": _entry(3, "2026-08-09T10:00:00")}
    server = {"s1": _entry(7, "2026-08-09T12:00:00")}
    assert _merge_value("progress", client, server)["s1"]["chapter"] == 7


def test_positions_from_both_devices_survive():
    """The regression this was written for.

    Phone is further through the book; laptop holds a position in an earlier
    chapter the phone has never opened. Neither may be lost.
    """
    phone  = {"s1": _entry(7, "2026-08-09T12:00:00", {"7": 0.8})}
    laptop = {"s1": _entry(3, "2026-08-09T10:00:00", {"3": 0.5})}

    out = _merge_value("progress", phone, laptop)["s1"]
    assert out["chapter"] == 7                    # newer device owns "where I am"
    assert out["positions"] == {"3": 0.5, "7": 0.8}


def test_newer_device_wins_a_chapter_both_have_read():
    phone  = {"s1": _entry(3, "2026-08-09T12:00:00", {"3": 0.9})}
    laptop = {"s1": _entry(3, "2026-08-09T10:00:00", {"3": 0.2, "4": 0.1})}

    out = _merge_value("progress", phone, laptop)["s1"]
    assert out["positions"] == {"3": 0.9, "4": 0.1}


def test_entry_without_positions_does_not_erase_one_that_has_them():
    """Records written before per-chapter positions existed have no map at all.

    A device still running an older build must not wipe the other's history
    simply by syncing later than it.
    """
    old_but_newer = {"s1": _entry(5, "2026-08-09T12:00:00")}          # no positions
    new_but_older = {"s1": _entry(3, "2026-08-09T10:00:00", {"3": 0.5})}

    out = _merge_value("progress", old_but_newer, new_but_older)["s1"]
    assert out["chapter"] == 5
    assert out["positions"] == {"3": 0.5}


def test_missing_side_returns_the_other():
    assert _merge_value("progress", None, {"s1": _entry(1, "x")}) == {"s1": _entry(1, "x")}
    assert _merge_value("progress", {"s1": _entry(1, "x")}, None) == {"s1": _entry(1, "x")}


def test_non_dict_payload_does_not_raise():
    """A corrupted or hand-edited localStorage blob must not 500 the sync."""
    assert _merge_value("progress", "nonsense", {"s1": _entry(1, "x")}) == "nonsense"
