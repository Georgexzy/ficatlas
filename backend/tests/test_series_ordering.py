"""Deciding what order a series goes in.

The Sacrifices Arc is the worked example: seven works, one stated position, and
the rest of the order sitting in phrases like "AU of GoF" — the canon book each
one diverges from. Neither the title matcher nor the sequel-chain matcher can
see that.

Publication date is the signal to be most careful with. It is genuinely useful
as a tiebreaker and quietly wrong as a primary: authors repost old work,
backdate, publish prequels years later, and bulk imports carry the import date.
These tests pin it to last place.
"""
from datetime import datetime

import pytest

from series_ordering import canon_position, order_members


class TestCanonPosition:
    @pytest.mark.parametrize("summary,expected", [
        ("AU of CoS, Slytherin!Harry. Harry goes back to Hogwarts.", 2),
        ("AU of GoF, Slytherin!Harry. Training his brother.", 4),
        ("AU of OoTP, Slytherin!Harry, HPDM slash.", 5),
        ("AU of HBP, HPDM slash. Revolution is never an easy choice.", 6),
        ("Alternate universe of Prisoner of Azkaban.", 3),
        ("A rewrite of Deathly Hallows.", 7),
        ("Canon divergence, Goblet of Fire.", 4),
    ])
    def test_real_sacrifices_style_summaries(self, summary, expected):
        assert canon_position(summary) == expected

    def test_post_and_pre_hyphenated(self):
        assert canon_position("Post-GoF, everyone is tired.") == 4
        assert canon_position("Set during PoA.") == 3

    def test_merely_mentioning_a_book_places_nothing(self):
        """"She reads Goblet of Fire on the train" is not a divergence point."""
        assert canon_position("She reads Goblet of Fire on the train.") is None
        assert canon_position("Spoilers for Deathly Hallows.") is None

    def test_otp_is_a_ship_not_the_order_of_the_phoenix(self):
        """The trap in the abbreviation list. OTP means one true pairing, and
        reading it as book five would misplace an enormous number of works."""
        assert canon_position("AU, my OTP finally gets together.") is None

    def test_no_summary(self):
        assert canon_position(None) is None
        assert canon_position("") is None
        assert canon_position("Just a story about people.") is None


class TestOrderMembers:
    def _m(self, wid, position=None, summary=None, published=None):
        return {"id": wid, "position": position, "summary": summary,
                "published_at": published}

    def test_declared_positions_win(self):
        got = order_members([self._m("b", position=2), self._m("a", position=1)])
        assert [m["id"] for m in got] == ["a", "b"]
        assert {m["position_source"] for m in got} == {"declared"}

    def test_canon_anchors_order_the_sacrifices_arc(self):
        """The whole point. One declared position, four canon anchors."""
        members = [
            self._m("brother", position=7, summary="AU, part 7 of Sacrifices."),
            self._m("song", summary="AU of HBP, HPDM slash."),
            self._m("mouth", summary="AU of CoS, Slytherin!Harry."),
            self._m("wind", summary="AU of OoTP, Slytherin!Harry."),
            self._m("freedom", summary="AU of GoF, Slytherin!Harry."),
        ]
        got = order_members(members)
        assert [m["id"] for m in got] == ["mouth", "freedom", "wind", "song", "brother"]
        assert [m["position"] for m in got] == [1, 2, 3, 4, 5]

    def test_a_declared_position_is_never_overwritten_by_a_canon_anchor(self):
        members = [self._m("a", position=1, summary="AU of DH."),
                   self._m("b", summary="AU of CoS.")]
        got = order_members(members)
        # 'a' says first and also mentions book seven; the author's word wins.
        assert [m["id"] for m in got] == ["a", "b"]
        assert got[0]["position_source"] == "declared"

    def test_without_a_dated_anchor_nothing_is_claimed(self):
        """The anchored member carries no date, so there is nothing to
        interpolate against. Guessing anyway is what this refuses to do."""
        members = [
            self._m("late", published=datetime(2020, 1, 1)),
            self._m("early", published=datetime(2010, 1, 1)),
            self._m("anchored", summary="AU of CoS."),
        ]
        got = order_members(members)
        assert got[0]["id"] == "anchored"
        assert got[0]["position_source"] == "canon"
        assert {m["position_source"] for m in got[1:]} == {"unknown"}

    def test_an_earlier_work_slots_in_FRONT_of_the_anchors(self):
        """Saving Connor opens the Sacrifices Arc and says so nowhere a machine
        can read — no position, no canon anchor, just the premise. What it has
        is the earliest publication date of the seven, and the anchored works
        around it give that date something to mean."""
        members = [
            self._m("mouth", summary="AU of CoS.", published=datetime(2005, 10, 9)),
            self._m("freedom", summary="AU of GoF.", published=datetime(2005, 12, 26)),
            self._m("connor", published=datetime(2005, 9, 15)),
        ]
        got = order_members(members)
        assert [m["id"] for m in got] == ["connor", "mouth", "freedom"]
        assert got[0]["position_source"] == "date"

    def test_an_undeclared_work_slots_BETWEEN_two_anchors(self):
        members = [
            self._m("mouth", summary="AU of CoS.", published=datetime(2005, 10, 9)),
            self._m("freedom", summary="AU of GoF.", published=datetime(2005, 12, 26)),
            self._m("maze", published=datetime(2005, 12, 25)),
        ]
        got = order_members(members)
        assert [m["id"] for m in got] == ["mouth", "maze", "freedom"]

    def test_a_later_work_slots_after_them(self):
        members = [
            self._m("mouth", summary="AU of CoS.", published=datetime(2005, 10, 9)),
            self._m("after", published=datetime(2009, 1, 1)),
        ]
        assert [m["id"] for m in order_members(members)] == ["mouth", "after"]

    def test_dates_never_outrank_a_real_signal(self):
        """A prequel published years later must not be sorted last."""
        members = [self._m("sequel", position=2, published=datetime(2005, 1, 1)),
                   self._m("prequel", position=1, published=datetime(2020, 1, 1))]
        got = order_members(members)
        assert [m["id"] for m in got] == ["prequel", "sequel"]

    def test_positions_are_contiguous_from_one(self):
        got = order_members([self._m("a", position=4), self._m("b", position=9)])
        assert [m["position"] for m in got] == [1, 2]

    def test_members_with_nothing_at_all_are_stable(self):
        got = order_members([self._m("b"), self._m("a")])
        assert [m["id"] for m in got] == ["a", "b"]
        assert {m["position_source"] for m in got} == {"unknown"}

    def test_every_member_survives(self):
        members = [self._m(str(i)) for i in range(6)]
        assert len(order_members(members)) == 6
