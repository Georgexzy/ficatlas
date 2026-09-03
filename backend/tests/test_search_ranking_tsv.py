"""The ranking tsvector must never become the MATCHING tsvector.

`_story_tsv()` is the expression `ix_stories_doc_fts` is built on, and the `@@`
predicate has to be written EXACTLY that way or Postgres cannot use the index —
the fallback is a sequential scan of 20M rows, which is a search that never
returns rather than a search that is slow.

`_story_tsv_ranked()` is a different expression over the same text, with the
fields kept apart by setweight() so ts_rank can tell a title from a tag. It is
deliberately NOT indexed: it is applied after retrieval, to the few thousand
rows already materialised as candidates.

The two are one edit apart, the mistake is invisible in review, and nothing
fails loudly if they are swapped — the results stay correct and the site stops
being usable. So this asserts the shape of each directly.
"""
import re

from api.search import (
    _REGCONFIG,
    _story_tsv,
    _story_tsv_ranked,
    _TSV_WEIGHTS_CATEGORY,
    _TSV_WEIGHTS_TITLE,
)


def _sql(expr) -> str:
    return str(expr.compile(compile_kwargs={"literal_binds": True}))


def test_matching_tsv_is_the_indexed_expression():
    """One to_tsvector over one fic_doc() call, and no setweight anywhere."""
    sql = _sql(_story_tsv())
    assert "fic_doc" in sql
    assert "setweight" not in sql.lower(), (
        "the matching expression must stay identical to ix_stories_doc_fts; "
        "adding setweight here silently drops the index and seq-scans 20M rows"
    )
    assert sql.lower().count("to_tsvector") == 1


def test_ranking_tsv_keeps_the_fields_apart():
    """Four weighted bands, concatenated — and never fic_doc, which is the
    flattening this exists to undo."""
    sql = _sql(_story_tsv_ranked()).lower()
    assert "fic_doc" not in sql, (
        "ranking over fic_doc() is the bug this replaced: it concatenates title, "
        "summary, author and every facet into one bag, so a word in the title "
        "counts exactly as much as the same word in a tag list"
    )
    for band in ("'a'", "'b'", "'c'", "'d'"):
        assert band in sql, f"missing setweight band {band}"
    assert sql.count("setweight") == 4
    # The title is band A and the tags are band B; the subject facets are C.
    assert re.search(r"title.*?'a'", sql, re.S), "title must be band A"


def test_weight_arrays_are_ordered_D_C_B_A():
    """ts_rank takes weights as {D, C, B, A}. Getting the order backwards is a
    silent, plausible-looking mistake: the search still works and ranks by
    summary text."""
    title = str(_TSV_WEIGHTS_TITLE).strip("'")
    category = str(_TSV_WEIGHTS_CATEGORY).strip("'")
    t = [float(x) for x in title.strip("{}").split(",")]
    c = [float(x) for x in category.strip("{}").split(",")]
    assert len(t) == len(c) == 4

    # Title query: the title (A, last) outranks every other band.
    assert t[3] == max(t), "title queries must weight the title highest"
    assert t[3] > t[2] > t[1] > t[0], "title weights must fall D < C < B < A"

    # Category query: TAGS (B) outrank the title (A). Searching a trope, being
    # tagged with it is evidence; being named after it is a coincidence.
    assert c[2] > c[3], (
        "category queries must weight tags above the title — otherwise "
        "'dramione' returns a drabble collection with the word in its title "
        "above the works actually tagged for the pairing"
    )
    assert c[0] == min(c), "summary/author stays the weakest band"


def test_regconfig_is_shared_by_both():
    """Both expressions must parse with the same dictionary, or a token that
    matched at retrieval can score zero at ranking."""
    assert "english" in str(_REGCONFIG)
    assert "english" in _sql(_story_tsv())
    assert "english" in _sql(_story_tsv_ranked())
