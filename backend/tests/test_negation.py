"""What the reader asked NOT to see.

From a corpus of real fic-finder posts: one request listed nine negative
conditions against six positive ones. None of it was parsed, so the words went
into the positive query and the search looked for stories CONTAINING them —
`harry potter no harem` returned "The Harem War" first and a work tagged
`Harry Potter Has a Harem` third. That is worse than no results: the reader is
handed the opposite of what they asked for, and it looks like it worked.
"""
import pytest
from sqlalchemy import text

from query_intent import resolve_intent


@pytest.fixture()
def vocab(db):
    db.execute(text("DELETE FROM facets"))
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag',    'Harems',          900),
          ('tag',    'Smut',          325862),
          ('tag',    'Angst',         868737),
          ('tag',    'Character Death', 40000),
          ('tag',    'Time Travel',     45960),
          ('tag',    'Fluff',         1130841),
          ('tag',    'Names',            800),
          ('tag',    'Grumpy Old Men',   300),
          ('fandom', 'Harry Potter',  686558)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM facets"))
    db.commit()


def _excluded(intent):
    return [v for g in intent.exclude_tag_groups for v in g]


@pytest.mark.parametrize("q,unwanted", [
    ("looking for harry potter fics no harem",   "Harems"),
    ("looking for dramione fics no smut",        "Smut"),
    ("recs please no angst",                     "Angst"),
    ("looking for a fic without character death", "Character Death"),
    ("looking for fics with no time travel",     "Time Travel"),
])
def test_a_stated_negative_becomes_an_exclusion(vocab, q, unwanted):
    assert unwanted in _excluded(resolve_intent(vocab, q))


def test_the_words_leave_the_positive_query(vocab):
    """The whole bug: "no harem" searched FOR harems, because the words stayed
    in the text that went to the full-text index."""
    i = resolve_intent(vocab, "looking for harry potter fics no harem")
    assert "harem" not in i.text.lower()
    assert "harry potter" in i.text.lower()


def test_only_the_negated_part_is_removed(vocab):
    """"no character death fluff" excludes the death and KEEPS the fluff — the
    tag window decides where the negation stops, which no amount of
    punctuation-guessing would have settled."""
    i = resolve_intent(vocab, "looking for fics no character death fluff")
    assert "Character Death" in _excluded(i)
    assert "fluff" in i.text.lower()


# The guard, and it is the reason `no`/`not` are gated on the query reading as
# a request. Measured with the gate off: all three of these became exclusions.
@pytest.mark.parametrize("title", [
    "no way home peter parker",
    "harry potter and the no name",
    "the boy who had no name",
    "no country for old men",
])
def test_a_title_containing_no_is_not_a_negation(vocab, title):
    assert _excluded(resolve_intent(vocab, title)) == []


def test_an_unresolvable_negation_changes_nothing(vocab):
    """Dropping the words would silently widen the search; excluding an
    unresolved phrase would exclude nothing while looking as though it had
    worked. Both are worse than leaving it alone."""
    i = resolve_intent(vocab, "looking for fics no zzzznonsensetrope")
    assert _excluded(i) == []
    assert "zzzznonsensetrope" in i.text.lower()


def test_without_needs_no_request_register(vocab):
    """`without` and `excluding` are unambiguous in a way `no` is not, so they
    fire on a bare query too."""
    assert "Smut" in _excluded(resolve_intent(vocab, "dramione without smut"))


def test_the_framing_stripper_no_longer_eats_without(vocab):
    """"looking for a fic without character death" became "a out character
    death": the framing pattern's `with` matched the first four letters of
    `without`, so the negation was destroyed before anything could read it."""
    i = resolve_intent(vocab, "looking for a fic without character death")
    assert "out" not in i.text.split()
    assert "Character Death" in _excluded(i)


@pytest.mark.parametrize("phrase,expect", [
    ("smut please", "Smut"),
    ("smut thanks", "Smut"),
    ("harems pls", "Harems"),
    ("character death tho", "Character Death"),
])
def test_trailing_politeness_is_not_the_thing_being_refused(db, phrase, expect):
    """Every politeness word is also a real tag, and the window matcher takes
    it — leaving the ACTUAL subject as leftover, which the positive path then
    searches FOR:

        "no smut please"  ->  excluded `please`,       "smut" left as a want
        "no smut thanks"  ->  excluded `Thanksgiving`, "smut" left as a want

    The second shows how bad it gets: the reader refused smut, and the search
    went looking for it having excluded a holiday instead. Same shape as "no
    harems" searching FOR harems, one layer down.
    """
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Smut',325862), ('tag','Harems',5180), ('tag','Harem',9000),
          ('tag','Character Death',88000), ('tag','please',3000),
          ('tag','Thanksgiving',3102), ('tag','Thanks',900)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    db.commit()
    from query_intent import _negated_subject
    tags, _ = _negated_subject(db, phrase)
    # The subject is present and the politeness is not. Asserting which
    # SPELLING leads would be asserting the resolver's ordering, which is a
    # separate rule with its own tests — "harems" legitimately returns both
    # `Harems` and `Harem`.
    assert expect in tags, tags
    for polite in ("please", "Thanksgiving", "Thanks", "thank you"):
        assert polite not in tags, tags
