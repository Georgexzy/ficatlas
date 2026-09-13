"""A request names as many tropes as it likes.

resolve_trope_tags finds the single longest window that is a tag and hands the
rest back as words. That is right for "time travel naruto", where the leftover
bounds the trope, and wrong for a fic-finder post, which is a LIST of
conditions — and those posts are the hardest and most valuable query this site
gets.
"""
import pytest
from sqlalchemy import text

from query_intent import resolve_intent


@pytest.fixture()
def vocab(db):
    db.execute(text("DELETE FROM facets"))
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag',    'Powerful Harry Potter',    653),
          ('tag',    'Powerful Harry',           529),
          ('tag',    'Albus Dumbledore Bashing', 3481),
          ('tag',    'Dumbledore Bashing',       942),
          ('tag',    'Time Travel',              45960),
          ('fandom', 'Naruto',                   458000)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM facets"))
    db.commit()


def test_two_tropes_in_one_request_both_resolve(vocab):
    """The reported failure. "powerful harry dumbledore bashing" resolved
    `Powerful Harry Potter` and threw "dumbledore bashing" at the text index,
    where it had to appear literally in a title, summary or author — so a query
    whose two halves are tags on 653 and 3,481 works returned nothing."""
    i = resolve_intent(vocab, "powerful harry dumbledore bashing")
    assert i.tags, "the first trope must still resolve"
    assert i.extra_tag_groups, "the second must resolve too, not become text"
    flat = [v for g in i.extra_tag_groups for v in g]
    assert any("Dumbledore Bashing" in v for v in flat)
    assert not i.tag_leftover.strip(), "nothing should be left as free text"


def test_a_bounding_word_is_still_left_as_text(vocab):
    """The behaviour the chain must NOT break. `Time Travel` is 45,960 works
    and "time travel naruto" must not return the 44,000 that are not Naruto —
    so "naruto" stays as a word bounding the tag rather than being swallowed."""
    i = resolve_intent(vocab, "time travel naruto")
    assert i.tags
    assert "naruto" in i.tag_leftover.lower() or not i.extra_tag_groups


def test_the_chain_is_bounded(vocab):
    """Each pass is another vocabulary lookup, and a request naming four
    separate tropes is rare enough not to pay for on every search."""
    i = resolve_intent(vocab, "powerful harry dumbledore bashing time travel")
    assert len(i.extra_tag_groups) <= 3


def test_a_single_trope_is_unchanged(vocab):
    i = resolve_intent(vocab, "dumbledore bashing")
    assert i.tags
    assert i.extra_tag_groups == []


# ── Explicit word counts ─────────────────────────────────────────────────────

from query_intent import read_request


@pytest.mark.parametrize("q,lo,hi", [
    ("at least 150k words",            150_000, None),
    ("over 100k words",                100_000, None),
    ("more than 200,000 words",        200_000, None),
    ("150k+ words",                    150_000, None),
    ("minimum of 80k words",            80_000, None),
    ("under 50k words",                   None, 50_000),
    ("less than 20k words",               None, 20_000),
    ("between 100k and 300k words",    100_000, 300_000),
])
def test_a_written_word_count_is_taken_literally(q, lo, hi):
    """"at least 150k words" used to set 50,000 — the vague `long` qualifier
    matched "very long" elsewhere in the post and silently replaced the number
    the reader had actually given. Being handed 50k fics when you asked for
    150k is worse than no filter, because it looks like it worked."""
    i = read_request(q)
    assert i.word_count_min == lo
    assert i.word_count_max == hi


@pytest.mark.parametrize("q", [
    "harry is lord of at least 2 houses",
    "fic with at least 3 horcruxes",
    "at least 4 siblings",
    "more than 2 years later",
])
def test_a_plot_detail_is_not_a_word_count(q):
    """The trap, and it is in the post this feature came from: "at least 2
    houses" is the identical comparator. A bare number only counts as a length
    when it carries a k/m suffix or reaches 1,000 — nobody asks for a fic over
    two words long."""
    i = read_request(q)
    assert i.word_count_min is None
    assert i.word_count_max is None


def test_a_title_containing_long_is_still_a_title():
    assert read_request("The Long Way Home").word_count_min is None


def test_the_vague_qualifier_still_works_on_its_own():
    """Nothing here replaces "long drabble fics" — it only stops a vague word
    overriding a precise one."""
    assert read_request("long drarry fics").word_count_min == 50_000
