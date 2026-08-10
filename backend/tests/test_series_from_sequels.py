"""Ordering a series from "Sequel to X" declarations.

The other detectors answer "do these belong together?". This one answers "which
comes first?", which is the part a reader needs — knowing seven works are a
series does not tell you where to start.

The failure to avoid is asserting an order the author did not write. A branch
(two sequels to the same story) is not a sequence, and a cycle is a
contradiction; both are dropped rather than resolved by guesswork.
"""
import pytest

from series_from_sequels import (build_edges, chains, extract_reference,
                                 normalise, resolve)


class TestExtractReference:
    @pytest.mark.parametrize("summary,expected", [
        ('Sequel to "Regina\'s Dream"', ("sequel", "Regina's Dream")),
        ("Sequel to Ren's Fiancee. Ren is determined to find out.",
         ("sequel", "Ren's Fiancee")),
        ("A NaruSaku Sequel to 'Let's Fall in Love'", ("sequel", "Let's Fall in Love")),
        ("Prequel to The Dark Forest", ("prequel", "The Dark Forest")),
        ("Sort of sequel (of sorts) to Always Expect the Unexpected",
         ("sequel", "Always Expect the Unexpected")),
    ])
    def test_shapes(self, summary, expected):
        assert extract_reference(summary) == expected

    def test_direction_is_captured(self):
        assert extract_reference("Sequel to Xanadu")[0] == "sequel"
        assert extract_reference("Prequel to Xanadu")[0] == "prequel"

    @pytest.mark.parametrize("summary", [
        "", None,
        "A story with no relatives.",
        # Names no work: the reference is to the thing you are already reading.
        "Sequel to this.",
        "sequel to my other story",
        "Prequel to the original",
    ])
    def test_refuses(self, summary):
        assert extract_reference(summary) is None


class TestResolve:
    TITLES = {"finding olivia": "id1", "saving elliot": "id2",
              "the dark forest": "id3", "run": "id4"}

    def test_exact(self):
        assert resolve("Finding Olivia", self.TITLES, "id2") == "id1"

    def test_punctuation_and_case_do_not_matter(self):
        assert resolve("finding  olivia!!", self.TITLES, "id2") == "id1"

    def test_the_captured_sentence_runs_past_the_title(self):
        """How most summaries are actually written."""
        assert resolve("Finding Olivia. She has been missing for a year now",
                       self.TITLES, "id2") == "id1"

    def test_a_work_never_resolves_to_itself(self):
        assert resolve("Finding Olivia", self.TITLES, "id1") is None

    def test_short_titles_are_not_matched_by_prefix(self):
        """"Run" is 3 characters; letting it match by prefix would attach it to
        any reference beginning with those letters."""
        assert resolve("Running away from everything", self.TITLES, "id9") is None

    def test_unknown_reference(self):
        assert resolve("Some Other Fic", self.TITLES, "id1") is None


class TestBuildEdges:
    def _w(self, wid, title, summary=""):
        return {"id": wid, "title": title, "summary": summary}

    def test_a_sequel_points_backwards(self):
        works = [self._w("a", "Finding Olivia"),
                 self._w("b", "Saving Elliot", "Sequel to Finding Olivia")]
        assert build_edges(works) == [("a", "b")]

    def test_a_prequel_points_forwards(self):
        works = [self._w("a", "The Dark Forest"),
                 self._w("b", "An Awkward Night", "Prequel to The Dark Forest")]
        assert build_edges(works) == [("b", "a")]

    def test_an_unresolvable_reference_makes_no_edge(self):
        works = [self._w("a", "Finding Olivia"),
                 self._w("b", "Saving Elliot", "Sequel to A Fic We Do Not Have")]
        assert build_edges(works) == []

    def test_no_self_edges(self):
        works = [self._w("a", "Finding Olivia", "Sequel to Finding Olivia")]
        assert build_edges(works) == []

    def test_duplicate_declarations_produce_one_edge(self):
        works = [self._w("a", "One"), self._w("b", "Two", "Sequel to One"),
                 self._w("c", "Two", "Sequel to One")]
        assert len(build_edges(works)) <= 2


class TestChains:
    def test_a_simple_run_orders(self):
        assert chains([("a", "b"), ("b", "c")]) == [["a", "b", "c"]]

    def test_a_pair(self):
        assert chains([("a", "b")]) == [["a", "b"]]

    def test_two_separate_series_stay_separate(self):
        got = chains([("a", "b"), ("x", "y")])
        assert sorted(got) == [["a", "b"], ["x", "y"]]

    def test_a_branch_is_not_a_reading_order(self):
        """Two sequels to the same story. The author wrote a fork, and emitting
        one arm as "the" order would invent a sequence."""
        assert chains([("a", "b"), ("a", "c")]) == []

    def test_a_merge_is_not_either(self):
        assert chains([("a", "c"), ("b", "c")]) == []

    def test_a_cycle_is_dropped(self):
        """"A is the sequel to B" and "B is the sequel to A" cannot both hold."""
        assert chains([("a", "b"), ("b", "a")]) == []

    def test_no_edges(self):
        assert chains([]) == []

    def test_a_work_appears_in_only_one_chain(self):
        got = chains([("a", "b"), ("b", "c"), ("x", "y")])
        flat = [w for run in got for w in run]
        assert len(flat) == len(set(flat))


class TestEndToEnd:
    def test_a_trilogy_orders_itself(self):
        works = [
            {"id": "3", "title": "The Last Case", "summary": "Sequel to Redemption."},
            {"id": "1", "title": "First Blood", "summary": "Where it starts."},
            {"id": "2", "title": "Redemption", "summary": "Sequel to First Blood."},
        ]
        assert chains(build_edges(works)) == [["1", "2", "3"]]
