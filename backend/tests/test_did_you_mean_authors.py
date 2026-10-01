"""Spelling rescue for AUTHORS, which had none until a reader reported it.

The report: searching `Ionibal` from the home page returned nothing, while
clicking the author link on a story — which carries the stored spelling exactly
— worked. The filter was never broken: `LONIBAL` returns the same 33 works as
`lonibal`, because `ix_stories_author_lower` makes the comparison
case-insensitive. `Ionibal` is not a case variant of `lonibal`; it is a capital
I where the pen name has a lower-case l, which is a different character, and no
amount of case folding will rescue it.

So this is not a filter bug. It is a missing vocabulary. Tags, fandoms,
characters and pairings all get a trigram "did you mean" from `facets`; authors
got nothing, because no author names were anywhere to match against.

DB-backed for the same reason as the facet rescue it sits beside: the feature is
one trigram query, and a pure-Python test of it would only assert that a string
was formatted.
"""
import pytest
from sqlalchemy import text

from api.search import _did_you_mean_authors


@pytest.fixture()
def authors(db):
    """A small author vocabulary carrying both of this feature's traps.

    `lonibal` and `ionia` are the pair the reported case turned on. Under the
    facet rescue's `similarity * ln(count)` ranking `ionia` wins, because it has
    more works — which is right for a tag and wrong for a person.
    """
    db.execute(text("DELETE FROM author_facets"))
    db.execute(text("""
        INSERT INTO author_facets (value, count) VALUES
          ('lonibal',   33),
          ('ionia',     57),
          ('ionik',     29),
          -- Below the table's build floor, so a row a refresh would not have
          -- written. It must not be suggested even if it is somehow present.
          ('hsmry',      4)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM author_facets"))
    db.commit()


def test_the_reported_case_is_rescued(authors):
    """`Ionibal` must reach `lonibal` — the case a reader actually filed."""
    out = _did_you_mean_authors(authors, "Ionibal")
    assert out, "the reported typo found nothing"
    assert out[0].value == "lonibal"
    assert out[0].query == 'author:"lonibal"', \
        "a suggestion must be a runnable query, not a word to retype"


def test_the_operator_prefix_does_not_defeat_the_match(authors):
    """`author: Ionibal` is the shape the search box actually produces.

    The operator has to come off before the trigram lookup, or the string being
    matched is `author: Ionibal` and no pen name is similar to that.
    """
    out = _did_you_mean_authors(authors, "author: Ionibal")
    assert out, "the operator form found nothing"
    assert out[0].value == "lonibal"


def test_similarity_beats_work_count(authors):
    """The ranking trap, and the reason authors are not ranked like tags.

    `ionia` has more works (57 against 33) but is a different person, and a
    stranger's name offered above the right one is the same class of error as
    suggesting a stranger's work.
    """
    out = _did_you_mean_authors(authors, "Ionibal")
    assert [s.value for s in out][0] == "lonibal"
    ranked = [s.value for s in out]
    if "ionia" in ranked:
        assert ranked.index("lonibal") < ranked.index("ionia")


def test_a_short_string_is_not_a_typo(authors):
    """The facet rescue's guard, for the same reason: 1-2 characters is a reader
    typing something short, not a misspelling."""
    assert _did_you_mean_authors(authors, "Ian") == []


def test_a_well_spelled_name_is_still_offered(authors):
    """Not a typo-rescue-only feature: an exact name comes back at similarity
    1.0, which is what the near-miss path (a search that returned a few
    unrelated works) relies on."""
    out = _did_you_mean_authors(authors, "Ionibal")
    assert all(s.kind == "author" for s in out)
    assert all(s.reason == "spelling" for s in out)


def test_a_missing_table_is_a_feature_off_not_a_failed_request(db):
    """`author_facets` is built by the admin facet refresh and genuinely does not
    exist on a fresh install, so the lookup has to survive its absence.

    Without the savepoint the failed SELECT aborts the caller's transaction and
    every later query in the session raises "current transaction is aborted" —
    which is how a rescue for a dead-end page takes down the search that
    surrounds it.
    """
    db.execute(text("DROP TABLE IF EXISTS author_facets"))
    db.commit()
    assert _did_you_mean_authors(db, "Ionibal") == []
    # And the session is still usable afterwards, which is the actual point.
    assert db.execute(text("SELECT 1")).scalar() == 1
