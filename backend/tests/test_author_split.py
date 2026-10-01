"""An author value ran to the end of the query, so the trailing words vanished.

`author: Ionibal fluff` parses as ONE author named "Ionibal fluff". It has to:
a bare multi-word operator value runs to the end of the text, because that is the
only thing that makes `tag:slow burn` and `author:Some Long Pen Name` work. No
syntactic rule separates those from an author followed by a phrase.

`_resolve_or_split` exists to make that separation using the vocabulary, and it
covered only columns living in `facets` — authors live in `author_facets`, so the
guard `col_name not in _FACET_KIND` sent every author straight past it. Measured
live before the fix:

    author: Ionibal            total=0   suggestions -> lonibal, ionia, ionik
    author: Ionibal fluff      total=0   suggestions -> 'Fluff Inc', 'fluffybluff',
                                                  'P.B. Fluff'

The second is the fault, not the emptiness: the rescue offered tag matches for a
word that was never the question. That is the `romoine` shape again — a wrong
answer that looks like an answer — one column over from the case
`_resolve_or_split` was written for.
"""
import pytest


@pytest.fixture
def known_author(monkeypatch):
    """Stand in for `author_facets` without needing the table to exist."""
    known = {"Jon Bellion", "MsKingBean89", "Lonibal", "senlinyu"}

    def _known(db, name):
        return name.strip() in known

    import api.search as search_mod
    monkeypatch.setattr(search_mod, "_author_is_known", _known)
    return known


def test_a_known_author_ends_the_author_half_of_the_query(known_author):
    from api.search import _resolve_or_split
    head, spill = _resolve_or_split(None, "author", "Jon Bellion fluff")
    assert head == "Jon Bellion"
    assert spill == ["fluff"]


def test_the_longest_known_author_wins(known_author):
    """Both halves are real authors in this fixture, and taking the shorter one
    would leave a real name sitting in the free-text bucket where it is matched
    against title and summary instead of the author column."""
    from api.search import _resolve_or_split
    head, spill = _resolve_or_split(None, "author", "MsKingBean89 fluff angst")
    assert head == "MsKingBean89"
    assert spill == ["fluff", "angst"]


def test_an_author_nobody_holds_is_left_alone(known_author):
    """A value nothing resolves is returned unchanged rather than chopped up: an
    unknown author should keep behaving as it did, not silently become a text
    search. This is the same rule `_resolve_or_split` already applies to every
    other column, and an unknown name is exactly when it matters most."""
    from api.search import _resolve_or_split
    assert _resolve_or_split(None, "author", "Nobody At All fluff") == (
        "Nobody At All fluff", [])


def test_a_single_word_author_is_never_split(known_author):
    """Nothing to split, and trying would be a chance to invent a boundary."""
    from api.search import _resolve_or_split
    assert _resolve_or_split(None, "author", "Ionibal") == ("Ionibal", [])


def test_the_exactness_of_the_lookup_is_what_makes_this_safe():
    """The job is to find where the author's name ends. A FUZZY match invents
    boundaries that are not there: `Ionibal fluff` trigram-matches the author
    `Ionibal` at 0.71 without any lookup being consulted at all. So the helper
    must be an equality test, and this asserts it stays one."""
    import inspect

    from api.search import _author_is_known
    src = inspect.getsource(_author_is_known)
    assert "WHERE value = :v" in src, "the author split must be an EXACT lookup"
    assert "similarity(" not in src and "% :v" not in src, (
        "a fuzzy author lookup would invent a boundary the reader never wrote"
    )
