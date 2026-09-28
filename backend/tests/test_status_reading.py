"""Which completion status a fic-finder post is ASKING FOR.

The rule was "first pattern that matches anywhere in the post wins", and the
`ongoing` pattern is listed first — so a single occurrence of "unfinished" beat
"completed" however many times the reader said it, wherever either appeared.

Found on a real post in the answers corpus:

    "I'm looking for completed Tom Riddle romance fics ... Must be completed —
     I am suffering from enough unfinished fics already"

which says completed twice, insists on it, and was searched as `wip`. The answer
the community gave that post has 32,966 kudos and is complete, so no reordering
of the results could ever have found it: the query asked for the opposite of what
was written.

Measured over the 420 most recent posts, the new rule changes 5 and leaves 415
alone — it is not churn.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.search import _read_status  # noqa: E402


def test_the_post_that_prompted_this():
    assert _read_status(
        "I'm looking for completed Tom Riddle romance fics. Must be completed "
        "- I am suffering from enough unfinished fics already") == "complete"


def test_a_repeated_status_beats_one_mentioned_in_passing():
    # The whole point of tallying rather than taking the first match.
    assert _read_status("completed please. must be complete. no unfinished") == "complete"


def test_a_complaint_is_not_a_request():
    # "no harems" searching FOR harems, one more time: the word is in the post
    # either way and only the words around it separate a want from a grievance.
    for text in ("I hate unfinished fics",
                 "completed fics please, I have too many unfinished ones",
                 "tired of wips",
                 "no wips please",
                 "not looking for anything unfinished"):
        assert _read_status(text) == "complete", text


def test_a_plain_request_still_reads_normally():
    assert _read_status("looking for ongoing drarry fics") == "ongoing"
    assert _read_status("give me your best wips") == "ongoing"
    assert _read_status("Looking for completed drarry fics please") == "complete"


def test_saying_either_is_not_a_preference():
    # A reader who says both equally has told you NOT to filter. Returning one
    # of them halves their results for no reason they gave.
    assert _read_status("I do not mind if it is complete or ongoing") is None
    assert _read_status("Can be complete (preferred) or WIP") is None


def test_the_plural_is_recognised():
    # `\bwip\b` does not match "wips", so "no wips please" read as no status at
    # all — and the plural is at least as common as the singular in these posts.
    assert _read_status("no wips please") == "complete"
    assert _read_status("any good wips?") == "ongoing"
    assert _read_status("no W.I.P.s") == "complete"


def test_nothing_said_is_nothing_applied():
    assert _read_status("looking for a drarry fic where harry is a teacher") is None
    assert _read_status("") is None
    assert _read_status(None) is None


def test_a_refusal_does_not_reach_across_a_sentence():
    # The clause is cut at the nearest sentence end, so an unrelated "no" in the
    # previous sentence cannot flip the status in this one.
    assert _read_status("No crossovers. Looking for ongoing fics.") == "ongoing"
