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


# ── The pairing is the subject of the request ────────────────────────────────

@pytest.fixture()
def ships(db):
    db.execute(text("DELETE FROM facets"))
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('character','Daphne Greengrass',6973), ('character','Daphne Bridgerton',2324),
          ('character','Harry Potter',152287), ('character','Harry',541),
          ('relationship','Daphne Greengrass/Harry Potter',1035),
          ('fandom','Harry Potter - J. K. Rowling',381225),
          ('tag','Fluff',1130841), ('tag','Fluffy',8964),
          ('tag','Romance',374075)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM facets"))
    db.commit()


def test_a_first_name_resolves_to_the_character_most_written(ships):
    """"Daphne" is four different people here. The most-written one is what a
    reader naming her without a surname means."""
    from api.search import _canonical_character
    assert _canonical_character(ships, "Daphne") == "Daphne Greengrass"


def test_slash_notation_resolves_to_the_actual_pairing(ships):
    """Looking each half up as a loose character was not enough: bare "Harry"
    is on 541 works and bare "Daphne" on 79, so both sank below every generic
    tag in the post and the pairing — the one thing asked for — never
    appeared."""
    out = extract(text="recommend me some Harry/Daphne fics that are fluffy", db=ships)
    assert out.terms and out.terms[0].kind == "relationship"
    assert out.terms[0].value == "Daphne Greengrass/Harry Potter"


def test_the_variant_spelling_is_swapped_for_the_one_archives_use(ships):
    """A reader writing "fluffy" means `Fluff` — 1,130,841 works against 8,964.
    Same word, two orders of magnitude apart, and only one is filed under."""
    out = extract(text="Harry/Daphne fics that are happy and fluffy", db=ships)
    values = [t.value for t in out.terms]
    assert "Fluff" in values and "Fluffy" not in values


def test_the_pairing_outranks_a_million_work_tag(ships):
    """The one exception to ranking by frequency. A pairing is the SUBJECT; a
    tag is a quality the reader wants it to have, and they want the second WITH
    the first rather than instead of it."""
    out = extract(text="Harry/Daphne fluffy romance", db=ships)
    assert out.terms[0].kind == "relationship"
    assert out.terms[0].count < 1_130_841        # Fluff is far bigger


def test_the_query_spends_its_slots_on_the_ship_and_qualities(ships):
    """Not on the fandom, which the pairing already implies — that would narrow
    nothing while dropping a quality the reader asked for."""
    out = extract(text="Harry/Daphne fics fluffy and romance", db=ships)
    assert out.query.startswith('ship:"Daphne Greengrass/Harry Potter"')
    assert "fandom:" not in out.query


# ── A bulleted post is a LIST of constraints, one per line ───────────────────

@pytest.fixture()
def bulleted(db):
    db.execute(text("DELETE FROM facets"))
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Albus Dumbledore Bashing',3481), ('tag','Dumbledore Bashing',942),
          ('tag','Harry is Lord Potter',82), ('tag','Lord Harry Potter',38),
          ('tag','Magically Powerful Harry',48),
          ('fandom','House',22251), ('tag','Sarcasm',3368), ('tag','Slytherin',1902)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM facets"))
    db.commit()


def test_the_already_read_list_is_not_a_list_of_wants(bulleted):
    """A post naming "sarcasm and slytherin" and "prince of slytherin" as fics
    ALREADY READ had `Sarcasm` and `Slytherin` returned as things it wanted.
    They are the opposite: works to exclude, and a taste signal."""
    out = extract(text=(
        "harry is lord of at least 2 houses\n"
        "I have read\n- sarcasm and slytherin - enjoyed\n- prince of slytherin"
    ), db=bulleted)
    values = [t.value for t in out.terms]
    assert "Sarcasm" not in values and "Slytherin" not in values
    assert "sarcasm and slytherin" in out.already_read


def test_a_written_word_count_comes_back_from_the_post(bulleted):
    out = extract(text="at least 150k words - very long\nharry is powerful",
                  db=bulleted)
    assert out.word_count_min == 150_000


def test_a_caveat_is_not_a_want(bulleted):
    """"bashing (dumbles/weasleys/hermione)- but not WAAAYYY TOOOO much" — the
    trailing clause defeated the window match entirely. Without it the line
    resolves; with it, nothing."""
    out = extract(text="bashing (dumbles/weasleys/hermione)- but not WAAAYYY TOOOO much",
                  db=bulleted)
    assert any("Bashing" in t.value for t in out.terms)


def test_the_biggest_spelling_of_a_concept_wins(bulleted):
    """The archives write one concept several ways and a single tag: operator
    cannot OR them, so picking the first silently chose a rare variant."""
    out = extract(text="dumbledore bashing", db=bulleted)
    assert out.terms[0].value == "Albus Dumbledore Bashing"   # 3,481, not 942


def test_a_line_concept_beats_a_word_that_merely_appeared(bulleted):
    """"harry is lord of at least 2 houses" became the word "houses", which
    matched `House` — the television programme."""
    out = extract(text="harry is lord of at least 2 houses", db=bulleted)
    assert out.terms[0].value != "House"


def test_the_concept_is_a_GROUP_of_spellings_not_one_of_them(bulleted):
    """The reported failure: a reader who had demonstrably read fics with
    lordship AND magical power AND politics was told there were none.

    `resolve_trope_tags` works from windows of the reader's own words, so it
    found `Magically Powerful Harry` — 48 works — and never saw
    `Magically Powerful Harry Potter`, the same concept on 1,719. Every step
    after that used the rare spelling: the probe found no co-occurrence and
    dropped a concept the post had asked for in as many words.
    """
    out = extract(text="harry is magically powerful", db=bulleted)
    t = out.terms[0]
    assert t.spellings, "a concept carries every spelling, not just its label"
    assert t.value in t.spellings


def test_a_longer_spelling_of_the_same_concept_is_found(bulleted):
    """`Powerful Harry` and `Powerful Harry Potter` are one concept written two
    ways; the archives' own usage decides which a search should carry."""
    from api.search import _biggest_spelling
    # ON CONFLICT, because the fixture already carries the short spelling —
    # inserting it again aborted the transaction and took the rest of the
    # module's teardown with it.
    bulleted.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Magically Powerful Harry Potter',1719)
        ON CONFLICT (kind, value) DO UPDATE SET count = EXCLUDED.count
    """))
    bulleted.commit()
    best = _biggest_spelling(bulleted, ["Magically Powerful Harry"])
    assert best == ("Magically Powerful Harry Potter", 1719)


# ── A request names a fandom, a status and a quality, not just tags ──────────

def test_an_abbreviated_fandom_becomes_a_fandom_not_a_word(bulleted):
    """"any good TWD fics" returned `twd` as a TAG on 163 works, while The
    Walking Dead is a fandom on 20,498. The alias table is mined from the
    naming convention (see fandom_aliases.py), so this generalises to fandoms
    nobody has heard of rather than being a list."""
    bulleted.execute(text("""
        CREATE TABLE IF NOT EXISTS fandom_aliases (
            alias text PRIMARY KEY, fandom text NOT NULL,
            works integer, built_at timestamp DEFAULT now())
    """))
    bulleted.execute(text("DELETE FROM fandom_aliases"))
    bulleted.execute(text("INSERT INTO fandom_aliases (alias, fandom, works) "
                          "VALUES ('twd','The Walking Dead (TV)',20498)"))
    bulleted.execute(text("""
        INSERT INTO facets (kind, value, count)
        VALUES ('fandom','The Walking Dead (TV)',20498)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    bulleted.commit()
    import query_intent
    query_intent._FANDOM_ALIASES, query_intent._FANDOM_ALIASES_AT = {}, 0.0
    out = extract(text="any good TWD fics, preferably ongoing", db=bulleted)
    assert out.terms and out.terms[0].kind == "fandom"
    assert "Walking Dead" in out.terms[0].value


@pytest.mark.parametrize("phrase,expected", [
    ("preferably ongoing",            "ongoing"),
    ("still being updated",           "ongoing"),
    ("wip is fine",                   "ongoing"),
    ("completed fics only",           "complete"),
])
def test_status_is_a_filter_not_a_word(bulleted, phrase, expected):
    """`ongoing` is a real tag on 450 works AND the status the reader asked
    for. Returned as a subject it spends a slot saying what the status filter
    already says, and says it worse."""
    out = extract(text=phrase, db=bulleted)
    assert out.status == expected
    assert "ongoing" not in [t.value.lower() for t in out.terms]


@pytest.mark.parametrize("phrase", [
    "any good fics", "something worth reading", "the best ones",
    "well-written please", "can you recommend anything",
])
def test_a_request_about_quality_becomes_a_sort(bulleted, phrase):
    """No tag expresses "good". What a reader means is the works other readers
    actually read, which is an ordering, not a filter."""
    assert extract(text=phrase, db=bulleted).sort == "popular"


def test_si_means_self_insert_not_a_fandom(bulleted):
    """`SI` is Self-Insert to every reader who types it. That it is also the
    initialism of SK8 the Infinity is a coincidence they will never have in
    mind — which is why it is in the alias miner's STOPLIST and in the tag
    abbreviation table instead."""
    from fandom_aliases import STOPLIST
    from api.search import _TAG_ABBREV
    assert "si" in STOPLIST
    assert _TAG_ABBREV["si"] == "Self-Insert"


@pytest.mark.parametrize("filler", ["or something", "tbh", "preferably", "laying"])
def test_conversational_filler_is_not_a_subject(filler):
    """Each of these is a real tag somebody has used — `or something` on 471
    works — which is exactly why the n-gram lookup keeps finding them."""
    from api.search import _is_grammar
    assert _is_grammar(filler)
