"""Pulling the searchable terms out of a whole fic-finder post.

From a real post asking for happy Harry/Daphne fics. Condensing it by
STRIPPING framing left a forty-word query that matched NOTHING, and the
outreach panel then produced a reply linking to an empty results page — the
exact thing its own posting rules forbid. Meanwhile
`harry potter daphne greengrass fluff` returns 1,058 works, so the search was
never the problem.
"""
import pytest
from sqlalchemy import text

from api.search import extract, _is_grammar


@pytest.fixture()
def vocab(db):
    db.execute(text("DELETE FROM facets"))
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Fluff',1130841), ('tag','Romance',374075),
          ('tag','Wholesome',5817), ('tag','Cute',54644),
          ('character','Daphne Greengrass',6973), ('character','Daphne',79),
          ('character','God',1113), ('character','Harry',541),
          ('fandom','Harry Potter',686558),
          -- Real facets, and real noise. People tag strange things.
          ('tag','i just',113), ('tag','I don''t',60), ('tag','one shots',1561)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM facets"))
    db.commit()


def test_grammar_is_not_a_subject(vocab):
    """`i just` is on 113 works and `I don't` on 60. Both beat `Fluff` in the
    first version of this, because it ranked by phrase length and two words
    beat one."""
    assert _is_grammar("i just") and _is_grammar("I don't")
    assert not _is_grammar("Daphne Greengrass")
    out = extract(text="I don't really care, i just need fluff", db=vocab)
    values = [t.value for t in out.terms]
    assert "i just" not in values and "I don't" not in values
    assert "Fluff" in values


def test_a_turn_of_phrase_does_not_outrank_the_subject(vocab):
    """"for the love of God" really does contain a character this index knows.
    Ranking by KIND put `God` (1,113 works) above every tag in the post; the
    archive's own usage is the honest signal."""
    out = extract(text="for the love of God recommend me something with fluff",
                  db=vocab)
    values = [t.value for t in out.terms]
    assert values.index("Fluff") < values.index("God")


def test_the_longest_match_wins_its_words(vocab):
    """"Daphne Greengrass" is taken and then "Daphne" is already spoken for —
    one term per stretch of the post."""
    out = extract(text="looking for Daphne Greengrass fics", db=vocab)
    values = [t.value for t in out.terms]
    assert "Daphne Greengrass" in values
    assert "Daphne" not in values


def test_slash_notation_is_how_readers_write_a_pairing(vocab):
    """The post that exposed all this says "Harry/Daphne" and "Harry x Daphne"
    and never once writes either full name. Splitting on punctuation loses the
    only mention of the second character."""
    out = extract(text="can someone recommend Harry/Daphne fics", db=vocab)
    assert any("Daphne" in t.value for t in out.terms)


def test_the_query_is_short_enough_to_match_something(vocab):
    """Three terms, not everything found. Every term is a requirement, so a
    query built from the whole post is the over-specified search this exists
    to replace."""
    out = extract(text="Harry Potter Daphne Greengrass fluff romance cute wholesome",
                  db=vocab)
    assert out.query.count(":") <= 3


def test_an_empty_post_is_not_an_error(vocab):
    out = extract(text="   ", db=vocab)
    assert out.terms == [] and out.query == ""
