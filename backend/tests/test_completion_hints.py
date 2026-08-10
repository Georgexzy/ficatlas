"""Reading "this is finished" out of a FanFiction.net summary.

The failure worth avoiding is the false positive: telling a reader a work is
complete when it is not, so they start it and find it abandoned mid-scene. A
false negative merely leaves the status as `unknown`, which is what it already
was. Everything here is weighted that way.
"""
import pytest

from completion_hints import declares_complete as done


class TestRealMarkers:
    """Shapes taken from actual summaries in the index."""

    @pytest.mark.parametrize("summary", [
        "COMPLETE!",
        "STORY COMPLETE! Confessed rapist Amelia Chase is Casey Novak's twin.",
        "Epilogue up COMPLETE! Elliot and Olivia explore a relationship.",
        "Not as epic as it sounds. [Oneshot: COMPLETE]",
        "p, ChaseCameron relationship. NOW COMPLETE!",
        "long awaited sequel to 'Harbinger' **COMPLETE**",
        "for each other since junior high. COMPLETE.",
        "(COMPLETE) Clark follows Lana on the plane to Paris.",
        "Mary Sue fanfiction, just like me! COMPLETED",
        "COMPLETED. AU. Senyum, air mata, doa, harapan.",
    ])
    def test_is_recognised(self, summary):
        assert done(summary)


class TestTheAdjectiveTrap:
    """"complete" is an ordinary word, and in fandom a very common adjective."""

    @pytest.mark.parametrize("summary", [
        "A complete AU where nobody dies.",
        "This is a complete rewrite of my old fic.",
        "Their first date was a complete disaster.",
        "COMPLETE AU — nothing from canon survives.",
        "A complete collection of drabbles.",
        "The complete works of a very tired author.",
        "complete crack, do not take seriously",
    ])
    def test_is_not_completion(self, summary):
        assert not done(summary)


class TestNegationsAndFutures:
    @pytest.mark.parametrize("summary", [
        "This is NOT COMPLETE, updates weekly.",
        "not complete yet, sorry!",
        "Nearly complete — one chapter to go.",
        "Will be complete by Christmas.",
        "To be completed soon.",
        "This story is far from complete.",
        "Never complete, I have no self control.",
        "Incomplete and likely to stay that way.",
        "in-complete, abandoned",
    ])
    def test_is_not_completion(self, summary):
        assert not done(summary)


class TestCaseMatters:
    def test_lower_case_alone_is_not_enough(self):
        """The single constraint doing most of the work. Authors shout the
        status and write the adjective normally, so requiring caps removes
        almost all the ambiguity without needing to understand the sentence."""
        assert not done("the story is complete")

    def test_mixed_case_is_not_enough(self):
        assert not done("The story is Complete")

    def test_caps_is(self):
        assert done("the story is COMPLETE")


class TestEdges:
    def test_empty_and_missing(self):
        assert not done("")
        assert not done(None)

    def test_the_word_inside_another_word_does_not_count(self):
        # "COMPLETENESS" and "INCOMPLETE" both contain the marker as a substring.
        assert not done("A study in COMPLETENESS and loss.")
        assert not done("INCOMPLETE")

    def test_a_reject_anywhere_vetoes_the_whole_summary(self):
        """Deliberately blunt: a summary containing both "COMPLETE!" and "not
        complete" is ambiguous, and ambiguous means leave it alone."""
        assert not done("COMPLETE! ...well, not complete, I lied.")

    def test_punctuation_around_the_marker(self):
        for s in ("[COMPLETE]", "(COMPLETE)", "**COMPLETE**", "COMPLETE.",
                  "COMPLETE!", "- COMPLETE -", "COMPLETE/EDITED"):
            assert done(s), s
