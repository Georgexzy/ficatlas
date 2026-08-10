"""Series the author declared in prose.

The title matcher cannot see these. Lightning on the Wave's Sacrifices Arc is
seven works with not one word in common across their titles — the sequence only
exists in the summaries, because FanFiction.net has no series feature and its
authors have always written it by hand.

The failure to avoid is a confident wrong grouping: unrelated works filed under
one name, telling a reader they are reading a sequence that does not exist. That
is worse than leaving them ungrouped, and most of these tests exist to hold that
line rather than to prove the happy path.
"""
import pytest

from series_from_summary import extract_declaration as extract, group_by_declaration


class TestRealDeclarations:
    def test_the_case_this_was_built_for(self):
        """Lightning on the Wave, "I Am Also Thy Brother"."""
        assert extract("AU, part 7 of Sacrifices. In the wake of death and "
                       "disaster, Harry struggles to be everything he is.") \
            == ("Sacrifices", 7)

    @pytest.mark.parametrize("summary,expected", [
        ("Part 1 of the Dangerverse.", ("Dangerverse", 1)),
        ("Book Two of the Chronicles of Somewhere", ("Chronicles of Somewhere", 2)),
        ("#3 in the Long Road series", ("Long Road", 3)),
        ("Second story in the Wayward saga", ("Wayward", 2)),
        ("Installment 4 of Blood and Water", ("Blood and Water", 4)),
        ("volume 2 of The Silver Age", ("Silver Age", 2)),
    ])
    def test_shapes(self, summary, expected):
        assert extract(summary) == expected

    def test_the_name_stops_at_the_sentence(self):
        """A summary keeps going after the declaration, and none of that is the
        series name."""
        name, pos = extract("part 2 of Sacrifices. Harry goes back to Hogwarts, "
                            "determined to protect his brother Connor.")
        assert (name, pos) == ("Sacrifices", 2)

    def test_series_and_the_bare_name_are_the_same_series(self):
        a = extract("Part 1 of the Sacrifices series")
        b = extract("Part 2 of Sacrifices")
        assert a[0] == b[0] == "Sacrifices"

    def test_universe_and_arc_suffixes_are_dropped(self):
        assert extract("Part 3 of the Sacrifices universe")[0] == "Sacrifices"
        assert extract("Part 3 of the Sacrifices arc")[0] == "Sacrifices"


class TestRefusals:
    @pytest.mark.parametrize("summary", [
        "",
        None,
        "Just a normal summary with no series in it at all.",
        # A relationship without a position. Real, and not enough to place a work
        # in a sequence, so it is deliberately ignored.
        "Sequel to Saving Connor.",
        "Set in my Sacrifices universe.",
        "A prequel to something I wrote years ago.",
    ])
    def test_yields_nothing(self, summary):
        assert extract(summary) is None

    @pytest.mark.parametrize("summary", [
        "Part 1 of the series",
        "Part 2 of this story",
        "part 3 of it",
        "Book 1 of the trilogy",
    ])
    def test_a_name_that_names_nothing_is_rejected(self, summary):
        """"the series" is not a series name. Grouping on it would file every
        author's unrelated works together under one meaningless heading."""
        assert extract(summary) is None

    def test_an_implausible_position_is_rejected(self):
        """"part 47 of" is a chapter reference or a typo, not a 47-book series
        declared inline."""
        assert extract("part 47 of something") is None

    def test_a_numeric_name_is_a_misread_sentence(self):
        assert extract("part 2 of 5") is None


class TestGrouping:
    def _work(self, i, summary):
        return {"id": f"id{i}", "title": f"Title {i}", "summary": summary}

    def test_two_agreeing_works_make_a_series(self):
        works = [self._work(1, "Part 1 of Sacrifices."),
                 self._work(2, "Part 2 of Sacrifices.")]
        groups = group_by_declaration(works)
        assert list(groups) == ["Sacrifices"]
        assert [m["position"] for m in groups["Sacrifices"]] == [1, 2]

    def test_one_work_alone_makes_nothing(self):
        """It tells us a series exists, not what else is in it. Inventing a
        one-member series adds noise and helps nobody."""
        assert group_by_declaration([self._work(1, "Part 2 of Sacrifices.")]) == {}

    def test_members_come_back_in_reading_order(self):
        works = [self._work(1, "Part 3 of Sacrifices."),
                 self._work(2, "Part 1 of Sacrifices."),
                 self._work(3, "Part 2 of Sacrifices.")]
        got = group_by_declaration(works)["Sacrifices"]
        assert [m["position"] for m in got] == [1, 2, 3]
        assert [m["id"] for m in got] == ["id2", "id3", "id1"]

    def test_duplicate_positions_are_refused(self):
        """Two works both claiming to be part 2 is an author reusing a phrase,
        not a sequence. Refusing beats guessing which is which."""
        works = [self._work(1, "Part 2 of Sacrifices."),
                 self._work(2, "Part 2 of Sacrifices.")]
        assert group_by_declaration(works) == {}

    def test_case_differences_are_the_same_series(self):
        works = [self._work(1, "Part 1 of SACRIFICES."),
                 self._work(2, "part 2 of sacrifices.")]
        assert len(group_by_declaration(works)) == 1

    def test_different_series_stay_apart(self):
        works = [self._work(1, "Part 1 of Sacrifices."),
                 self._work(2, "Part 2 of Sacrifices."),
                 self._work(3, "Part 1 of Dangerverse."),
                 self._work(4, "Part 2 of Dangerverse.")]
        assert sorted(group_by_declaration(works)) == ["Dangerverse", "Sacrifices"]

    def test_works_without_declarations_are_ignored(self):
        works = [self._work(1, "Part 1 of Sacrifices."),
                 self._work(2, "Part 2 of Sacrifices."),
                 self._work(3, "A completely unrelated oneshot.")]
        assert len(group_by_declaration(works)["Sacrifices"]) == 2


class TestMixedEvidence:
    """One position plus one mention is a series. This is the rule that makes
    the case the whole module exists for actually work."""

    def _w(self, i, summary):
        return {"id": f"id{i}", "title": f"T{i}", "summary": summary}

    def test_the_sacrifices_arc(self):
        """Real summaries. Exactly one of the seven gives a position, and one
        other names the series without placing itself."""
        works = [
            self._w(1, "AU, part 7 of Sacrifices. In the wake of death and disaster."),
            self._w(2, "AU, short story set in my Sacrifices universe. James Potter faces a choice."),
            self._w(3, "AU of CoS, Slytherin!Harry. Harry goes back to Hogwarts."),
        ]
        groups = group_by_declaration(works)
        assert list(groups) == ["Sacrifices"]
        members = groups["Sacrifices"]
        assert {m["id"] for m in members} == {"id1", "id2"}
        # The one with no declaration is not swept in.
        assert "id3" not in {m["id"] for m in members}

    def test_a_mention_alone_is_not_a_series(self):
        """Without a positional declaration there is no name we trust."""
        works = [self._w(1, "Set in my Sacrifices universe."),
                 self._w(2, "Also set in the Sacrifices universe.")]
        assert group_by_declaration(works) == {}

    def test_extended_members_have_no_invented_position(self):
        works = [self._w(1, "part 3 of Sacrifices."),
                 self._w(2, "part 1 of Sacrifices."),
                 self._w(3, "A oneshot in the Sacrifices verse.")]
        members = group_by_declaration(works)["Sacrifices"]
        positions = {m["id"]: m["position"] for m in members}
        assert positions["id2"] == 1 and positions["id1"] == 3
        assert positions["id3"] is None

    def test_a_work_is_only_claimed_once(self):
        works = [self._w(1, "part 1 of Alpha."), self._w(2, "part 2 of Alpha."),
                 self._w(3, "part 1 of Beta."), self._w(4, "part 2 of Beta."),
                 self._w(5, "Set in the Alpha universe and the Beta universe.")]
        groups = group_by_declaration(works)
        seen = [m["id"] for ms in groups.values() for m in ms]
        assert len(seen) == len(set(seen))
