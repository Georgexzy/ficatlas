"""The trope lookup against a real `facets` table.

The pure half is in test_query_intent.py. This is the half that only means
anything with a vocabulary in front of it: which tag a reader's phrase resolves
to, which phrases must resolve to nothing, and the two guards that stop it
hijacking a query that was never about a trope.

The rows below are the real values and real orders of magnitude from the live
index — the cases that each guard was written for.
"""

import os
import sys

import pytest
from sqlalchemy import text as sql_text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query_intent import (resolve_intent, resolve_trope_tags, _alias_expand,
                          _THING_CACHE, _TROPE_SQL_CACHE)

FACETS = [
    ("tag", "Slytherin Harry Potter", 2450),
    ("tag", "Slytherin Harry", 490),
    ("tag", "Harry Potter Raises Teddy Lupin", 208),
    ("tag", "Wandmaker Harry Potter", 37),
    ("tag", "Time Travel", 45960),
    ("tag", "Alternate Universe - Time Travel", 7759),
    ("tag", "Alternate Universe - No Time Travel", 273),
    ("tag", "Alternate Universe - Coffee Shops & Cafés", 20078),
    ("tag", "Omegaverse", 9489),
    ("tag", "Hurt/Comfort", 577244),
    ("tag", "this took way too long to write", 23),
    ("tag", "all the young dudes", 13),
    ("tag", "Inspired by All the Young Dudes - MsKingBean89", 225),
    ("tag", "Haikyuu - Freeform", 415),
    ("tag", "traveller - Freeform", 16),
    ("fandom", "Toy Story", 1473),
    ("fandom", "Haikyuu!!", 120000),
    ("fandom", "Haikyuu", 415),
    ("character", "Sasuke Uchiha", 90000),
    ("relationship", "dramione", 134),
]


@pytest.fixture()
def vocab(db):
    # Both caches are process-wide and keyed by phrase, so a value cached from
    # another test's fixture would answer here instead of the rows below.
    _THING_CACHE.clear()
    _TROPE_SQL_CACHE.clear()
    for kind, value, count in FACETS:
        db.execute(sql_text(
            "INSERT INTO facets (kind, value, count, norm) VALUES "
            "(:k, :v, :c, regexp_replace(lower(:v), '[^a-z0-9]+', '', 'g'))"),
            {"k": kind, "v": value, "c": count})
    db.commit()
    yield db
    _THING_CACHE.clear()
    _TROPE_SQL_CACHE.clear()


def test_word_order_does_not_matter(vocab):
    """The headline case. The archive files it as `Slytherin Harry Potter`;
    a reader types it either way round and must get the same works."""
    for phrase in ("slytherin harry", "harry slytherin"):
        tags, works, leftover, whole = resolve_trope_tags(vocab, phrase)
        assert "Slytherin Harry Potter" in tags
        assert works == 2450
        assert whole is True and leftover == ""


def test_a_sentence_resolves(vocab):
    tags, works, _, whole = resolve_trope_tags(vocab, "harry raises teddy")
    assert tags == ["Harry Potter Raises Teddy Lupin"]
    assert works == 208 and whole is True


def test_alias_bridges_a_word_the_archive_does_not_use(vocab):
    """"wandcrafter" appears nowhere in the vocabulary; `Wandmaker` is what it
    is filed under, and only the alias table connects them. The caller passes
    the rewritten spellings in — they are tried before the reader's own."""
    variants, _ = _alias_expand("wandcrafter harry")
    tags, _, _, _ = resolve_trope_tags(vocab, "wandcrafter harry", variants)
    assert tags == ["Wandmaker Harry Potter"]


def test_without_the_rewrite_the_vocabulary_has_nothing(vocab):
    """The other half of the same fact: the reader's own word resolves to
    nothing at all, which is why it has to be rewritten rather than widened."""
    assert resolve_trope_tags(vocab, "wandcrafter harry") == ([], 0, "", False)


def test_leftover_words_come_back(vocab):
    """`Time Travel` is 45,960 works and a Naruto reader wants a few thousand
    of them. The words the tag did not account for are returned so the caller
    can AND them onto the branch."""
    tags, works, leftover, whole = resolve_trope_tags(vocab, "time travel naruto")
    assert "Time Travel" in tags
    assert leftover == "naruto" and whole is False


def test_negated_tag_is_not_the_answer(vocab):
    tags, _, _, _ = resolve_trope_tags(vocab, "time travel")
    assert "Alternate Universe - No Time Travel" not in tags


def test_a_fandom_is_not_a_trope(vocab):
    """`Toy Story` is a 1,473-work fandom. Resolving it to the works tagged
    with Toy Story references replaced the fandom with fanworks about it."""
    assert resolve_trope_tags(vocab, "toy story") == ([], 0, "", False)


def test_a_ship_nickname_is_not_a_trope(vocab):
    assert resolve_trope_tags(vocab, "dramione") == ([], 0, "", False)


def test_a_fandom_inside_the_query_is_not_a_window(vocab):
    """"long haikyuu fics" reached here as "haikyuu" and resolved to
    `Haikyuu - Freeform` — the fandom wearing a tag's clothes."""
    tags, _, _, _ = resolve_trope_tags(vocab, "haikyuu complete")
    assert "Haikyuu - Freeform" not in tags


def test_a_reference_tag_is_not_a_trope(vocab):
    """The 225-work `Inspired by All the Young Dudes` must not make the
    most-read work on the site look like a genre."""
    tags, works, _, _ = resolve_trope_tags(vocab, "all the young dudes")
    assert tags == ["all the young dudes"]
    assert works == 13


def test_author_chatter_is_not_a_trope(vocab):
    assert resolve_trope_tags(vocab, "the long way home") == ([], 0, "", False)


def test_one_word_needs_words_around_it(vocab):
    """A bare one-word query cannot open a tag branch: the text search already
    matches every tag containing it, so the branch would only widen."""
    assert resolve_trope_tags(vocab, "omegaverse") == ([], 0, "", False)
    tags, _, leftover, _ = resolve_trope_tags(vocab, "omegaverse bakugou")
    assert tags == ["Omegaverse"] and leftover == "bakugou"


def test_a_small_one_word_tag_is_noise(vocab):
    """`traveller - Freeform`, 16 works. A one-word trope worth resolving is
    one everybody has heard of, and those are all large."""
    tags, _, _, _ = resolve_trope_tags(vocab, "sasuke is a time traveller")
    assert "traveller - Freeform" not in tags


def test_unknown_phrase_resolves_to_nothing(vocab):
    assert resolve_trope_tags(vocab, "levi adopts eren") == ([], 0, "", False)


def test_an_unbounded_branch_over_a_huge_tag_is_not_worth_adding(vocab):
    """`Hurt/Comfort` is 577,244 works and "hurt comfort" leaves nothing over to
    bound it. The branch can only re-match what the text search already
    matched — a work tagged Hurt/Comfort has both words in its document by
    definition — and it turned the query into a 20s statement timeout and a
    503. The resolution is KEPT, because it still tells the ranker this is a
    category query and that costs nothing.
    """
    intent = resolve_intent(vocab, "hurt comfort")
    assert "Hurt/Comfort" in intent.tags
    assert intent.tag_works == 577244
    assert intent.tag_branch_ok is False


def test_the_same_tag_is_fine_once_something_bounds_it(vocab):
    """"hurt comfort geralt" is the same 577,244-work tag AND one more word,
    which is a narrow branch and returns in 3.4s."""
    intent = resolve_intent(vocab, "hurt comfort geralt")
    assert intent.tag_leftover == "geralt"
    assert intent.tag_branch_ok is True


def test_an_ordinary_trope_still_joins_the_predicate(vocab):
    intent = resolve_intent(vocab, "harry raises teddy")
    assert intent.tag_branch_ok is True
