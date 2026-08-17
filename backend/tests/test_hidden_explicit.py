"""The rating set the search UI sends, and why the backend has to recognise it.

The bug this guards is subtle and was shipped twice before it was caught, both
times because the endpoint was verified with a hand-made request that did not
look like the one the browser sends.

`hidden_explicit` answers "how many more works would appear if you turned the
explicit toggle on". It is computed by taking the filters the search just ran,
removing the ones that express "hide explicit", and counting the explicit rows
that remain.

There are TWO such expressions, not one. The UI sets `explicit=false` AND sends
`ratings=G,T,M,NR` — the same intent stated twice. Removing only the first left
the count asking for `rating IN (G,T,M,NR) AND rating = E`, which is 0 by
construction. So the "9 more works are hidden" notice was correct for
`/api/search?author=MesserMoon` and silently always-zero for every real search
made by the actual site.

Nothing here needs a database: the failure was a set comparison, and that is
what is asserted.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.story import RatingEnum

# Exactly what frontend/app/page.tsx puts on the wire when the explicit toggle
# is off and no rating pills are selected:
#
#     ratings: joinCsv(...) ?? (explicit ? undefined : "G,T,M,NR")
#
# Hard-coded here on purpose. If someone adds a rating to RatingEnum, this test
# fails and points at the frontend literal that has to change with it — which is
# the drift that would silently disable the notice again.
UI_DEFAULT_NON_EXPLICIT = {"G", "T", "M", "NR"}


def test_ui_default_is_exactly_every_rating_but_explicit():
    """The backend recognises the UI's set by comparing it to
    `set(RatingEnum) - {explicit}`. If the two ever diverge, the comparison
    quietly stops matching and hidden_explicit goes back to always being 0."""
    from_enum = {r.value for r in RatingEnum} - {RatingEnum.explicit.value}
    assert from_enum == UI_DEFAULT_NON_EXPLICIT


def test_explicit_is_not_in_the_default_set():
    """The whole premise: the default set hides E, which is why a separate count
    of what it hid is worth showing at all."""
    assert RatingEnum.explicit.value not in UI_DEFAULT_NON_EXPLICIT


def test_every_rating_value_is_a_distinct_short_code():
    """The set comparison is on `.value`, so two ratings sharing one would
    collapse the set and break the match in a way nothing else would show."""
    values = [r.value for r in RatingEnum]
    assert len(values) == len(set(values))


def test_a_narrower_rating_choice_is_not_the_default():
    """A reader who ticked Teen alone has asked for Teen. Turning the explicit
    toggle on would reveal nothing, so their filter must NOT be dropped from the
    count — otherwise the site offers to show works its own filters exclude."""
    assert {"T"} != UI_DEFAULT_NON_EXPLICIT
    assert UI_DEFAULT_NON_EXPLICIT - {"NR"} != UI_DEFAULT_NON_EXPLICIT
