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


# ---------------------------------------------------------------------------
# Generalising the ground truth across the index.
#
# Each of these was found by running the SAME request shape against twelve
# fandoms — TWD, MCU, ASOIAF, ATLA, MHA, PJO, AOT, HXH, TVD, Naruto, Star Wars,
# Supernatural — rather than by fixing the one post that was reported. A rule
# derived from a single post is a patch; a rule that holds across twelve
# unrelated vocabularies is an extraction rule.
# ---------------------------------------------------------------------------

def test_the_query_string_carries_the_status_and_the_length(bulleted):
    """Every filter that the search bar can express goes INTO the query string.

    The endpoint returned `status` and `word_count_min` as fields beside
    `query`, and the outreach panel — the one caller written for it — read
    `query` and dropped both. So a post saying "ongoing, at least 150k words"
    was searched with neither constraint, and nothing anywhere said so.

    The query string is what a caller runs and what a reader pastes, so it has
    to be the whole request. `parse_query` reads these back and `/api/search`
    re-parses `q`, which is what makes one string enough.
    """
    out = extract(text="any fics, preferably ongoing, at least 150k words",
                  db=bulleted)
    assert out.status == "ongoing"
    assert out.word_count_min == 150000
    assert "wip" in out.query.split()
    assert "words:>150k" in out.query

    from query_parser import parse_query
    back = parse_query(out.query)
    assert back.status == "in_progress"
    assert back.word_count_min == 150000


def test_a_word_count_is_written_in_the_shorthand_the_bar_parses(bulleted):
    """k/m suffixes are REQUIRED. `words:>150000` parses to nothing at all —
    silently — which is the same shape of bug as the status one."""
    from api.search import _k
    from query_parser import parse_query
    for n in (1000, 50000, 150000, 1500000):
        assert parse_query(f'tag:"x" words:>{_k(n)}').word_count_min == n


def test_the_abbreviation_belongs_to_the_fandom_and_nothing_else(bulleted):
    """`asoiaf` and `tvd` are fandom initialisms AND real freeform tags, so the
    n-gram lookup found them a second time and the query came out
    `fandom:"A Song of Ice and Fire…" tag:"ASoIaF"` — the tag narrowing to the
    few works that carry the abbreviation, inside the fandom it had already
    selected. A word consumed by one mechanism must not be spent again by
    another. Measured: TVD 23 works against 2,742."""
    bulleted.execute(text("""
        CREATE TABLE IF NOT EXISTS fandom_aliases (
            alias text PRIMARY KEY, fandom text NOT NULL,
            works integer, built_at timestamp DEFAULT now())
    """))
    bulleted.execute(text("DELETE FROM fandom_aliases"))
    bulleted.execute(text("INSERT INTO fandom_aliases (alias, fandom, works) "
                          "VALUES ('tvd','The Vampire Diaries (TV)',10309)"))
    bulleted.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('fandom','The Vampire Diaries (TV)',10309),
          ('tag','tvd',1200)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    bulleted.commit()
    import query_intent
    query_intent._FANDOM_ALIASES, query_intent._FANDOM_ALIASES_AT = {}, 0.0
    out = extract(text="good tvd fics", db=bulleted)
    assert [t.value for t in out.terms if t.kind == "fandom"] == \
        ["The Vampire Diaries (TV)"]
    assert "tvd" not in [t.value.lower() for t in out.terms if t.kind != "fandom"]
    assert 'tag:"tvd"' not in out.query


def test_a_bare_first_name_resolves_to_the_character_archives_file(ships):
    """A reader writes the name they say out loud and the archives file the
    full one. `Damon` is a character on 76 works and `Damon Salvatore` on
    thousands, so "damon centric tvd fics" was narrowed to the handful whose
    character list says only "Damon".

    `_resolve_pair` has canonicalised each half of a PAIRING this way since it
    was written. The rule was simply never applied to a character found loose
    in the prose, which is how most of them arrive."""
    ships.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('character','Damon',76), ('character','Damon Salvatore',9134)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    ships.commit()
    out = extract(text="good fics, damon centric", db=ships)
    chars = [t for t in out.terms if t.kind == "character"]
    assert chars and chars[0].value == "Damon Salvatore"


def test_a_rarer_full_name_is_not_the_canonical_spelling(ships):
    """Only ever UP. A "canonical" spelling with fewer works than the bare name
    is not the canonical spelling — it is a different person who happens to
    share a first name."""
    ships.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('character','Damon',760), ('character','Damon Nobody',80)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    ships.commit()
    out = extract(text="good fics, damon centric", db=ships)
    chars = [t.value for t in out.terms if t.kind == "character"]
    assert "Damon Nobody" not in chars and "Damon" in chars


def test_both_ends_of_the_spectrum_means_no_length_filter(bulleted):
    """"longfics and one-shots welcome" is a reader saying they do not care.

    It reads as `word_count_min = 50,000` from one phrase and
    `word_count_max = 10,000` from another, and together that is
    `words:50k-10k` — a range no work can satisfy. A contradiction is not a
    narrow request, so both ends are dropped rather than one kept arbitrarily.

    Invisible until the word count went INTO the query string, because the only
    caller read `query` and ignored the fields. A filter nothing applies cannot
    be seen to be wrong."""
    out = extract(text="recommend me some fluffy fics, "
                       "longfics and one-shots welcome", db=bulleted)
    assert out.word_count_min is None and out.word_count_max is None
    assert "words:" not in out.query


def test_the_words_that_asked_for_a_length_are_spent(bulleted):
    """`Words` is a real tag on 254 works and `at least` on 186. Both come
    straight out of "at least 150k words", both say nothing about any story,
    and both were competing for one of three slots with what the post was
    actually about."""
    from api.search import _is_length_word
    assert _is_length_word("Words") and _is_length_word("at least")
    assert not _is_length_word("Albus Dumbledore Bashing")

    bulleted.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Words',254), ('tag','at least',186)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    bulleted.commit()
    out = extract(text="- at least 150k words - very long", db=bulleted)
    assert out.word_count_min == 150000
    assert "Words" not in [t.value for t in out.terms]
    assert "at least" not in [t.value for t in out.terms]


def test_a_bare_first_name_ranks_behind_what_the_post_spelled_out(ships):
    """"harry is lord of at least 2 houses" names a concept AND, from the
    single word "harry", a character on 152,287 works. The second is
    technically true of the request and says almost nothing about it, and on
    frequency alone it won — so the query spent two of three slots on
    `char:"Harry Potter"` and `char:"Hermione Granger"` (the name the reader
    wanted BASHED) and had none left for lordship or politics.

    Same asymmetry as the rejected "characters outrank tags" rule, one level
    down: a name that merely APPEARS in a sentence is weaker evidence than a
    concept the sentence was written to express."""
    ships.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Harry is Lord Potter',82)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    ships.commit()
    out = extract(text="harry is lord potter", db=ships)
    values = [t.value for t in out.terms]
    if "Harry Potter" in values and "Harry is Lord Potter" in values:
        assert values.index("Harry is Lord Potter") < values.index("Harry Potter")


@pytest.fixture()
def xover(db):
    db.execute(text("DELETE FROM facets"))
    db.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Crossover',88000), ('tag','Self-Insert',20498),
          ('fandom','Naruto',150000), ('fandom','Bleach',40000)
    """))
    db.commit()
    yield db
    db.execute(text("DELETE FROM facets"))
    db.commit()


def test_no_crossovers_does_not_search_for_crossovers(xover):
    """"no crossovers please" came back as `tag:"Crossover"` — the search asked
    for the one thing the reader had ruled out.

    Same failure as "no harems" searching FOR harems, in a new place, and for
    the same reason: the word is in the post either way, so only the words
    AROUND it separate a want from a refusal. The refusal is therefore tested
    FIRST, because "no crossovers" contains "crossovers"."""
    out = extract(text="any good fics, self-insert, no crossovers please",
                  db=xover)
    assert out.crossovers == "exclude"
    assert "xover:exclude" in out.query
    assert "Crossover" not in [t.value for t in out.terms]


@pytest.mark.parametrize("phrase", [
    "no crossovers", "not a crossover", "non-crossover",
    "without crossovers", "no cross-overs", "avoid crossovers",
])
def test_every_way_a_reader_refuses_a_crossover(xover, phrase):
    assert extract(text=f"looking for fics, {phrase}", db=xover).crossovers \
        == "exclude"


def test_asking_for_a_crossover_asks_for_one(xover):
    """The other direction has to keep working, or the refusal pattern has
    simply swallowed the want."""
    out = extract(text="looking for a naruto/bleach crossover", db=xover)
    assert out.crossovers == "only"
    assert "xover:only" in out.query


def test_a_post_that_never_mentions_crossovers_gets_no_filter(xover):
    """Deliberately NOT a default, and the measurement is the reason.

    `is_crossover` is `len(fandoms) > 1`, and AO3 authors routinely tag several
    spellings of ONE franchise — `Star Wars - All Media Types` beside
    `Star Wars: The Clone Wars`. Over a 20,000-work sample of flagged AO3
    works, 33% have fandoms that all share a first word, i.e. are not
    crossovers at all; on the real TWD post, 74 works with 22 flagged and 11 of
    those wrongly. Excluding by default would delete a third of the answer,
    half of it for a bug, and do it invisibly — fewer results look exactly like
    a search that worked. It would also skew the archive mix: AO3 is 16.19%
    flagged against FF.net's 5.33%, and that gap is coverage, not fact."""
    out = extract(text="any good self-insert fics", db=xover)
    assert out.crossovers is None
    assert "xover:" not in out.query


def test_two_spellings_of_one_concept_do_not_both_become_requirements(xover):
    """"self-insert" came out as `tag:"Self-Insert" tag:"Self Insert"`.

    Different facet values, the same concept, AND-ed — so the query demanded a
    work carrying BOTH spellings, which is the narrowest possible reading of a
    reader who wrote the concept once. `_implies` cannot see it: word-boundary
    containment compares characters, and a hyphen is not a space. The loser is
    not discarded, it joins the winner's spellings so the probe still ORs
    them."""
    xover.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES ('tag','Self Insert',9000)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    xover.commit()
    out = extract(text="looking for a self-insert fic", db=xover)
    assert out.query.count("tag:") <= 1, out.query
    tags = [t for t in out.terms if t.kind == "tag"]
    assert tags
    assert "Self-Insert" == tags[0].value
    assert any("self insert" in sp.lower() for sp in tags[0].spellings)


def test_a_tag_that_negates_the_concept_is_not_a_spelling_of_it():
    """`resolve_trope_tags` matches on the reader's words, so asking for
    self-inserts put `Not a self insert` in the same group — and the group is
    what the probe ORs, so a concept could be kept on the strength of works
    saying the opposite of what was asked."""
    from api.search import _NEGATED_TAG
    for neg in ("Not a self insert", "No Beta", "Non-Canon", "Anti Dumbledore",
                "not a crossover"):
        assert _NEGATED_TAG.match(neg), neg
    for real in ("Self-Insert", "Nobody Dies", "Nonbinary Character",
                 "Note Passing"):
        assert not _NEGATED_TAG.match(real), real


# ---------------------------------------------------------------------------
# Measured against a corpus of fifteen REAL fic-finder posts rather than
# invented ones. Before this pass: three returned nothing at all, and six ran
# in the wrong fandom — which is worse, because a search in the wrong fandom
# still returns thousands of works and nothing about it looks broken.
# ---------------------------------------------------------------------------

def test_a_sign_off_is_not_the_subject(bulleted):
    """"Thanks a lot!" produced `tag:"Thanksgiving"` on three separate posts,
    and "Please can you recommend" produced `tag:"Please Don't Hate Me"`.

    Two mechanisms, both fixed: `_biggest_spelling` matched a prefix of a WORD
    rather than of a word SEQUENCE, so `Thanks` reached `Thanksgiving` and
    `Close` reached `Closeted Character`; and framing was left in the line the
    trope resolver saw."""
    from api.search import _is_framing, _strip_framing
    assert _is_framing("Thanks")
    assert _is_framing("Thanksgiving", matched="thanks")
    assert not _is_framing("Time Travel")
    assert _strip_framing("Thanks a lot") == ""
    assert "Snape" in _strip_framing("Please can you recommend me good Snape fics")


def test_framing_is_stripped_before_the_trope_resolver_sees_the_line():
    """The resolver matches a WINDOW of the reader's words, so framing does not
    merely add noise — it changes which window wins. "I like the idea of Time
    travel" resolved to `it seemed like a good idea at the time`, with "travel"
    left over as a bare word."""
    from api.search import _strip_framing
    assert _strip_framing("I like the idea of Time travel").lower() == "time travel"


def test_typographic_apostrophes_are_normalised(bulleted):
    """`’` is not the ASCII `'` every character class here is written with, so
    "I’ll" tokenised as "I" + "ll" — and `ll` is the mined initialism for
    `League of Legends`, which then led the query on a hurt/comfort post."""
    out = extract(text="I’ll read anything, I’ve loved fluff for years", db=bulleted)
    assert "League of Legends" not in [t.value for t in out.terms]
    assert "Fluff" in [t.value for t in out.terms]


def test_a_name_that_identifies_nobody_is_not_a_character(ships):
    """`None` is a character facet on 6,314 works, from "none the wiser". So
    are `The Author`, `Main Character` and `Myself`. Their size made them
    outrank the characters a post was actually about."""
    from api.search import _is_non_entity, _is_generic_entity
    for junk in ("None", "The Author", "Main Character", "Myself", "Reader"):
        assert _is_non_entity(junk), junk
    # An OC is a real want and a useless fandom hint — both must be true.
    assert not _is_non_entity("Original Characters")
    assert _is_generic_entity("Original Characters")


def test_the_probe_runs_the_predicate_the_search_runs():
    """Third time this bit: word count, then status, now the content gates.

    `Regulus Black/James Potter` with `Sounding` is 14 works to a probe that
    ignores the gates and **0** once they are applied — so the probe kept a
    junk tag that reduced the reader's search to nothing."""
    import inspect
    from api.search import _probe_count
    src = inspect.getsource(_probe_count)
    for predicate in ("gate_underage", "gate_adult", "is_crossover",
                      "word_count", "status"):
        assert predicate in src, predicate


# ---------------------------------------------------------------------------
# From a real post: "Does anyone have any Juvia Locker/male reader
# recommendations. My medium of choice is AO3 and I do not mind nsfw. Btw
# Juvia Lockser is from the anime Fairy Tail". Four separate things missed.
# ---------------------------------------------------------------------------

def test_a_pairing_half_may_be_more_than_one_word():
    """The pattern captured `[A-Za-z]{3,}` a side, so "Juvia Locker/male
    reader" gave ("Locker", "male") — a surname without its given name, and an
    adjective. Most characters are not called one word."""
    from api.search import _PAIR_RE, _trim_pair_half
    m = _PAIR_RE.search("Does anyone have any Juvia Locker/male reader recs")
    assert m
    assert _trim_pair_half(m.group(1)) == "Juvia Locker"
    assert _trim_pair_half(m.group(2)) == "male reader"


def test_a_misspelt_half_still_resolves(ships):
    """"Juvia Locker" has the surname wrong — the reader corrects themselves
    two lines later. Every contiguous run is tried, longest first, so `Juvia`
    prefix-matches `Juvia Lockser`. Shrinking from one end only was not enough:
    the error can be in either half of a name."""
    ships.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('character','Juvia Lockser',2479), ('character','Reader',188474),
          ('character','Male Reader',1958)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    ships.commit()
    from api.search import _resolve_half
    assert _resolve_half(ships, "Juvia Locker", True) == "Juvia Lockser"
    assert _resolve_half(ships, "me some Harry", True) == "Harry Potter"


def test_a_reader_insert_collapses_to_the_general_reader(ships):
    """`Reader` is a character on 188,474 works, `Male Reader` on 1,958 — so
    the specific spelling is the one that co-occurs with nothing. Measured:
    `Juvia Lockser` with `Male Reader` is 0 works and with `Reader` is 26."""
    ships.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('character','Reader',188474), ('character','Male Reader',1958)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    ships.commit()
    from api.search import _collapse_reader
    assert _collapse_reader(ships, "Male Reader", 1958) == ("Reader", 188474)
    # Not every two-word name is a spelling of something shorter.
    assert _collapse_reader(ships, "Harry Potter", 152287) is None


def test_both_halves_become_characters_when_no_pairing_exists(ships):
    """There is no `Juvia Lockser/Reader` among the 1,568 Juvia pairings, and
    returning nothing because the pairing is unattested throws away a request
    the index can answer — works carrying both characters."""
    ships.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('character','Juvia Lockser',2479), ('character','Reader',188474),
          ('character','Male Reader',1958)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    ships.commit()
    from api.search import _pair_characters
    got = {t.value for t in _pair_characters(ships, "any Juvia Locker/male reader recs")}
    assert got == {"Juvia Lockser", "Reader"}


@pytest.mark.parametrize("phrase,expect", [
    ("My medium of choice is AO3", "ao3"),
    ("preferably on ao3", "ao3"),
    ("I only read on ff.net", "ffnet"),
    ("anything on fanfiction.net", "ffnet"),
    # The negative forms must say NOTHING rather than pin the wrong archive.
    ("anywhere but AO3", None),
    ("not on ao3 please", None),
    ("no preference", None),
])
def test_the_archive_the_reader_actually_uses(phrase, expect):
    from api.search import _read_site
    assert _read_site(phrase) == expect


@pytest.mark.parametrize("phrase,expect", [
    ("I do not mind nsfw", True), ("smut is fine", True),
    ("18+ is fine", True), ("no smut please", False),
    ("sfw only", False), ("I'll read anything", None),
])
def test_nsfw_permission_is_read_but_never_linked(phrase, expect, bulleted):
    """PERMISSION, not a want — and it stays out of the query string.

    The adult gate is what keeps a shared link safe, and `OutreachPanel` strips
    `explicit` from every link it builds. A reader saying this on their own
    post has consented for themselves, not for whoever they paste a link to."""
    from api.search import _reads_nsfw
    assert _reads_nsfw(phrase) is expect
    out = extract(text=f"looking for fluff fics, {phrase}", db=bulleted)
    assert "explicit" not in out.query
    assert "rating:" not in out.query


@pytest.mark.parametrize("post", [
    "any harry potter fics with underage sex, explicit is fine",
    "harry potter dead dove fics",
    "some fluffy drarry fics please",
    "juvia/male reader fics, I do not mind nsfw",
    "good twd fics, ongoing, SI main character",
])
def test_a_link_builder_is_never_handed_an_unsafe_query(bulleted, post):
    """The INVARIANT, not a table of expected verdicts.

    Whether a given post produces a gated term in its query depends on what
    survives the probe, which depends on the vocabulary — so asserting "this
    post must refuse" is really asserting something about the fixture, and the
    first version of this test did exactly that and failed for the right
    reason. What must always hold is the implication: if the query names
    something unsafe, the caller is told to refuse.
    """
    bulleted.execute(text("""
        INSERT INTO facets (kind, value, count) VALUES
          ('tag','Underage',22968), ('tag','Underage Sex',10037),
          ('tag','dead dove',302), ('tag','Explicit Sexual Content',41000),
          ('fandom','Harry Potter',686558)
        ON CONFLICT (kind, value) DO NOTHING
    """))
    bulleted.commit()
    from api.search import _link_is_unsafe
    out = extract(text=post, db=bulleted)
    if _link_is_unsafe(out.query) or out.gated_terms:
        assert out.link_unsafe, (out.query, out.gated_terms)
    # And the query itself never carries a toggle, whatever the post said.
    for forbidden in ("include_underage", "explicit=", "rating:"):
        assert forbidden not in out.query.lower(), out.query


def test_consent_never_reaches_the_query_or_a_link(bulleted):
    """`explicit_ok` is information for the operator and nothing else. It must
    not appear in the query string, and it must not be able to make a link
    safer — only the operator better informed."""
    out = extract(text="juvia/male reader fics, I do not mind nsfw",
                  db=bulleted)
    assert out.explicit_ok is True
    for forbidden in ("explicit", "include_underage", "rating:"):
        assert forbidden not in out.query.lower(), out.query


@pytest.mark.parametrize("query,unsafe", [
    # The gate lists are EXACT, so a query can name a neighbour they do not
    # hold. This is the case that slipped through an exact-match check:
    ('fandom:"Harry Potter" tag:"Sexual Content"', True),
    ('tag:"Porn With Plot"', True),
    ('tag:"dead dove don\'t eat"', True),
    ('fandom:"Harry Potter" tag:"Underage"', True),
    ('tag:"Rape/Non-con Elements"', True),
    # And the false friends, which must stay linkable. An identity tag is not a
    # content warning, and underage DRINKING is on record as a thing this
    # codebase does not over-block.
    ('tag:"Asexual Character" tag:"Slow Burn"', False),
    ('tag:"Demisexual Character"', False),
    ('fandom:"Harry Potter" tag:"Underage Drinking"', False),
    ('tag:"Sexuality Crisis"', False),
    ('ship:"Draco Malfoy/Harry Potter" tag:"Fluff"', False),
    ('fandom:"The Walking Dead (TV)" tag:"Self-Insert" wip', False),
])
def test_whether_a_query_is_safe_to_paste_in_public(query, unsafe):
    """A DIFFERENT question from "should this work be hidden", with the
    asymmetry inverted.

    The gate lists are deliberately exact because over-blocking hides tens of
    thousands of ordinary works. That is right for deciding what a search
    RETURNS and wrong for deciding what a URL may SAY: "harry potter explicit
    language and sexual content fics" produced `tag:"Sexual Content"` and an
    exact-match check flagged nothing, because the list holds
    `Explicit Sexual Content` and not that neighbour. The works were gated; the
    text was not, and the text is what gets pasted.

    Refusing a link costs the operator a link — they reply in words instead —
    while a bad link costs a ban. Over-matching is the correct shape here and
    only here.
    """
    from api.search import _link_is_unsafe
    assert _link_is_unsafe(query) is unsafe, query
