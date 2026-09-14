"""The gate term lists must have exactly one home.

They were written out twice — in `content_gates.py`, which owns the database
trigger, and again in `api/search.py`, which owns the query filters — and they
drifted, in the direction that matters:

    UNDERAGE_TAGS   content_gates 31   api/search 16   (15 missing)
    ADULT_TAGS      content_gates 60   api/search 29   (31 missing)

`api/search.py` was the laxer copy and was missing the most serious terms in
the set: `Child Sexual Abuse`, `Child Grooming`, `Statutory Rape`,
`Adult/Minor Relationship`, `Ephebophilia`, `Dubious Consent`, `Family Incest`.

The gate COLUMNS were still computed from the fuller list, so the trigger and
the backfill were right. What was checking the short list was every
belt-and-braces array check in the search path — which exists precisely for the
rows a backfill has not reached, i.e. exactly when the short list is all there
is.

CLAUDE.md had already written the rule this broke, about `api/hubs.py`: "two
lists of what counts as this content would drift, and the one that drifts laxer
is the bug." The copy nobody noticed was one level up.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_consumer_shares_one_list_object():
    """Identity, not equality. Equal contents today is how the drift started."""
    import gate_terms as g
    import content_gates as cg
    from api.search import (_ADULT_TAGS, _ADULT_WARNINGS,
                            _UNDERAGE_TAGS, _UNDERAGE_WARNINGS)
    from api.hubs import _ADULT_TAGS as h_at, _UNDERAGE_TAGS as h_ut

    assert cg.UNDERAGE_TAGS is g.UNDERAGE_TAGS is _UNDERAGE_TAGS is h_ut
    assert cg.ADULT_TAGS is g.ADULT_TAGS is _ADULT_TAGS is h_at
    assert cg.UNDERAGE_WARNINGS is g.UNDERAGE_WARNINGS is _UNDERAGE_WARNINGS
    assert cg.ADULT_WARNINGS is g.ADULT_WARNINGS is _ADULT_WARNINGS


def test_nobody_redefines_them():
    """A second assignment anywhere is the drift starting again."""
    pat = re.compile(r"^\s*_?(?:UNDERAGE|ADULT)_(?:TAGS|WARNINGS)\s*=\s*\[", re.M)
    offenders = []
    for f in ROOT.rglob("*.py"):
        if f.name in ("gate_terms.py",) or "tests" in f.parts:
            continue
        if pat.search(f.read_text()):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"gate terms redefined outside gate_terms.py: {offenders}"


def test_the_terms_a_ban_was_issued_over_are_present():
    """The specific thing that cost a reader fourteen days, plus the serious
    terms the drifted copy was missing."""
    import gate_terms as g
    assert "Underage Sex" in g.UNDERAGE_WARNINGS
    for t in ("Child Sexual Abuse", "Child Grooming", "Statutory Rape",
              "Adult/Minor Relationship", "Pedophilia"):
        assert t in g.UNDERAGE_TAGS, t
    for t in ("Dead Dove: Do Not Eat", "Rape/Non-con Elements", "Incest",
              "Dubious Consent"):
        assert t in g.ADULT_TAGS, t


def test_the_lists_stay_exact_values_not_patterns():
    """`Underage Drinking` (25,321 works), `Underage Smoking` and
    `Underage Kissing` are not sexualisation of minors, and a `chan%` pattern
    matches `Chance Meetings`. Over-blocking hides tens of thousands of ordinary
    stories and teaches people the filter is broken."""
    import gate_terms as g
    for benign in ("Underage Drinking", "Underage Smoking", "Underage Drug Use",
                   "Underage Kissing", "Chance Meetings"):
        assert benign not in g.UNDERAGE_TAGS, benign
    for lst in (g.UNDERAGE_TAGS, g.ADULT_TAGS):
        for t in lst:
            assert "%" not in t and "*" not in t, t


def test_mental_health_themes_are_not_gated():
    """`Self-Harm`, `Suicidal Thoughts`, `Suicide` and `Eating Disorders` hid
    **130,581 works** from every default search that carried no other
    adult-tier reason — behind a toggle labelled "Show explicit & adult
    content", which does not describe them. Measured: `Suicidal Thoughts`
    returned 2,003 works by default against 5,000 with the toggle on, and
    `Eating Disorders` 594 against 5,000.

    The tier answers one question: could this link get removed, or the person
    who pasted it banned. A fic tagged `Suicidal Thoughts` is not that, and
    none of the community rules this was built from reaches mental-health
    themes. The archives already show their own warnings on the work page, so
    hiding the work from search adds no warning — it removes the story.

    Asserted so that re-adding one is a deliberate act with a failing test in
    front of it, not a tidy-up."""
    import gate_terms as g
    for t in ("Self-Harm", "Suicide", "Suicidal Thoughts", "Eating Disorders"):
        assert t not in g.ADULT_TAGS, t
        assert t not in g.UNDERAGE_TAGS, t


def test_violence_and_non_con_are_still_gated():
    """The other direction, so the removal above cannot widen. "Extreme or
    encouraged violence/rape fic must be linked with a clear warning" is an
    explicit rule, and these serve it."""
    import gate_terms as g
    for t in ("Torture", "Graphic Torture", "Mutilation", "Snuff",
              "Rape/Non-con Elements", "Dead Dove: Do Not Eat", "Incest",
              "Bestiality", "Dubious Consent"):
        assert t in g.ADULT_TAGS, t
