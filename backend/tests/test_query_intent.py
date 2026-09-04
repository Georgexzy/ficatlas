"""Unit tests for query_intent — the layer that reads a reader's own phrasing.

Everything here is the PURE half plus the vocabulary-facing helpers that can be
exercised without a database. The tag lookup itself needs `facets` and is
covered by test_query_intent_tags.py against the throwaway DB.

The failure this module exists to stop is quiet: `websearch_to_tsquery` ANDs
every term, so "long drarry fics" is `long AND drarry AND fic` and returns 68
works where "drarry" returns 5,000. Nothing errors — the reader is simply shown
a short list and concludes the index is thin.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query_intent import (_alias_expand, _stem, _tag_coverage, _trope_tokens,
                          read_request, _REFERENCE_TAG_RE)


# ── Framing ──────────────────────────────────────────────────────────────────

def test_fic_noun_and_relative_clause_go():
    assert read_request("fics where harry is a slytherin").text == "harry is a slytherin"


def test_request_opener_goes():
    assert read_request(
        "looking for a fic where harry raises teddy").text == "harry raises teddy"


def test_recs_prefix_goes():
    assert read_request("recs for slow burn destiel").text == "slow burn destiel"


def test_trailing_politeness_goes():
    assert read_request("time travel naruto please").text == "time travel naruto"


def test_framing_never_strips_to_nothing():
    # "fanfiction" alone is a search, not a frame around one.
    assert read_request("fanfiction").text == "fanfiction"
    assert read_request("fic").text == "fic"


def test_bare_story_is_left_alone():
    """`story` is only framing inside a request phrase. On its own it is a
    title word — Toy Story, Ghost Story — and stripping it changed which
    fandom the reader was asking for."""
    assert read_request("toy story").text == "toy story"
    assert read_request("a ghost story").text == "a ghost story"


def test_story_where_is_framing():
    assert read_request("stories where zuko joins the gaang").text == "zuko joins the gaang"


# ── Length and status become filters, but only in a request ──────────────────

def test_long_in_a_request_is_a_word_count():
    got = read_request("long drarry fics")
    assert got.text == "drarry"
    assert got.word_count_min == 50_000
    assert [t["key"] for t in got.tokens] == ["word_count"]


def test_long_in_a_title_is_a_word():
    """The gate. `The Long Way Home` has no request framing, so nothing here
    fires and the word stays part of the search."""
    got = read_request("the long way home")
    assert got.text == "the long way home"
    assert got.word_count_min is None


def test_long_fic_carries_its_own_register():
    got = read_request("long fic sterek")
    assert got.word_count_min == 50_000


def test_one_shot_is_a_ceiling():
    assert read_request("oneshot fics klance").word_count_max == 10_000


def test_qualifier_alone_is_not_a_filter():
    """"one shot" with nothing else is a search for the phrase — a filter with
    no query left to filter is a browse of the whole index."""
    got = read_request("one shot")
    assert got.text == "one shot"
    assert got.word_count_max is None


def test_natural_language_status():
    got = read_request("fics where thorin lives finished")
    assert got.status == "complete"


def test_stranded_determiner_goes_only_after_framing():
    assert read_request("any fics where thorin lives").text == "thorin lives"
    assert read_request("the arithmancer").text == "the arithmancer"


# ── Tokenising for the vocabulary lookup ─────────────────────────────────────

def test_bang_syntax_splits():
    assert _trope_tokens("dark!harry") == ["dark", "harry"]


def test_stopwords_go():
    assert _trope_tokens("harry is a slytherin") == ["harry", "slytherin"]


# ── Rewriting the reader's word into the archive's ───────────────────────────

def test_alias_produces_every_spelling_best_first():
    """All three are searched beside what the reader typed. `wandcrafter` finds
    3 works on its own; the archive writes it `wandmaker`, `wandcrafting` or
    `wandlore`, and a summary is the only place an FF.net work can say so."""
    variants, shorthand = _alias_expand("wandcrafter harry")
    assert variants == ["wandmaker harry", "wandcrafting harry", "wandlore harry"]
    assert shorthand is True


def test_no_alias_no_variants():
    assert _alias_expand("slytherin harry") == ([], False)


def test_one_alias_per_query():
    """Two would be a question about two tropes at once, which the ordinary
    text search answers better than a guess would."""
    variants, _ = _alias_expand("abo h/c")
    assert all("hurt comfort" not in v for v in variants)


def test_short_alias_widens_but_does_not_rank():
    """"mod" is real fandom shorthand and also the title word of 242 works,
    most of them about Minecraft. It may add a branch; it may not turn a title
    search into a category one."""
    variants, shorthand = _alias_expand("mod harry")
    assert variants == ["master of death harry"]
    assert shorthand is False


# ── Coverage: how much of the tag the reader actually named ──────────────────

def test_real_trope_clears_the_bar():
    assert _tag_coverage("Slytherin Harry Potter", ("harry", "slytherin")) > 0.55
    assert _tag_coverage("Harry Potter Raises Teddy Lupin",
                         ("harry", "raises", "teddy")) > 0.55
    assert _tag_coverage("Time Travel", ("time", "travel")) == 1.0


def test_author_chatter_does_not():
    """The tag that made `the long way home` resolve to a trope. It contains
    both words and means nothing; two words of five is not enough of it."""
    assert _tag_coverage("this took way too long to write", ("long", "way")) < 0.55


def test_ao3_wrapper_is_not_counted():
    """`Alternate Universe - ` is furniture, not part of the trope's name."""
    assert _tag_coverage("Alternate Universe - Coffee Shops & Cafés",
                         ("coffee", "shop")) > 0.55


def test_word_must_survive_the_parenthetical():
    """"harry" in `Dark Mark (Harry Potter)` is the fandom disambiguator, not
    the trope — so the tag does not answer "dark!harry" at all."""
    assert _tag_coverage("Dark Mark (Harry Potter)", ("dark", "harry")) == 0.0


def test_word_start_not_substring():
    """`long` is inside `along`, and matching it there is how a title query
    resolved to author chatter."""
    assert _tag_coverage("tags will be added along the way", ("long", "way")) == 0.0


# ── Tags that name another work rather than describe this one ────────────────

def test_reference_tags_are_recognised():
    assert _REFERENCE_TAG_RE.match("Inspired by All the Young Dudes - MsKingBean89")
    assert _REFERENCE_TAG_RE.match("Podfic of Something")
    assert _REFERENCE_TAG_RE.match("Song: All the Young Dudes (Mott the Hoople)")


def test_ordinary_tags_are_not():
    assert not _REFERENCE_TAG_RE.match("Slytherin Harry Potter")
    assert not _REFERENCE_TAG_RE.match("Basement Dwelling")


# ── The shapes people actually type ──────────────────────────────────────────
#
# Every query in this block is a real subject line from one of the two
# long-running Harry Potter fic-finder communities on LiveJournal
# (hpficfinders, potterficfinder), where the request has to be the post title
# and so carries its framing in the same breath. The FORM is not
# fandom-specific — only the nouns inside it are.

def test_fic_search_prefix():
    """"Fic search: Hermione's parents kidnapped a girl" returned 0 works: the
    literal words "fic" and "search" were required of every result."""
    assert read_request(
        "Fic search: Hermione's parents kidnapped a girl"
    ).text == "Hermione's parents kidnapped a girl"


def test_help_im_looking_for():
    got = read_request("Help! I'm looking for a deleted Harry/Draco story")
    assert got.text == "Harry/Draco"


def test_help_finding():
    assert read_request("Help finding a fanfic about time travel").text == "time travel"


def test_the_one_where():
    assert read_request("the one where sirius adopts harry").text == "sirius adopts harry"


def test_request_adjectives_go():
    """"an old Snarry fanfiction" searched for works containing the word
    "old" — 119 of them, led by "Dear Old Snakes"."""
    assert read_request("Looking for an old Snarry fanfiction").text == "Snarry"
    assert read_request("Searching for a specific drarry fic").text == "drarry"


def test_request_adjectives_are_gated():
    """Outside a request they are ordinary words. `Old Man Logan` is a title."""
    assert read_request("old man logan").text == "old man logan"


def test_naming_the_archive_is_a_filter():
    got = read_request("looking for a drarry fic on AO3")
    assert got.site == "ao3"
    assert "ao3" not in got.text.lower()
    assert [t["key"] for t in got.tokens] == ["sites"]


def test_archive_named_by_domain():
    assert read_request("time travel fics on fanfiction.net").site == "ffnet"


# ── Matching a word however the archive inflected it ─────────────────────────

def test_stem_strips_safe_suffixes():
    """The archives write the same trope every way round —
    `Sirius Black Raises Harry Potter` (236 works),
    `Harry Potter was Raised by Sirius Black` (51). Matching the literal word
    found the small one."""
    assert _stem("raised") == "rais"
    assert _stem("raising") == "rais"
    assert _stem("adopts") == "adopt"
    assert _stem("joins") == "join"
    assert _stem("lives") == "live"


def test_stem_leaves_short_words_alone():
    """Never below four characters: a three-letter prefix anchored at a word
    start matches most of the vocabulary."""
    assert _stem("goes") == "goes"
    assert _stem("hug") == "hug"


def test_stem_does_not_strip_er():
    """`er`/`ers` would turn "traveller" into "travell" and "master" into
    "mast", which stops matching the words they came from."""
    assert _stem("traveller") == "traveller"
    assert _stem("master") == "master"


def test_stem_handles_a_dropped_apostrophe():
    assert _stem("doesnt") == "doesn"


# ── Words for the artefact, not for what is in it ────────────────────────────

def test_artefact_nouns_are_not_content():
    """A bare "story" survives the framing pass when it is not part of a
    request phrase, and resolved through the stem `stor` to `Storytelling` and
    `Storybrooke` — a 2,120-work tag branch off a word meaning "fanfic"."""
    assert _trope_tokens("harry draco story") == ["harry", "draco"]


def test_tokens_are_deduplicated():
    """A rewritten spelling can repeat a word: "ewe" becomes "epilogue what
    epilogue". The same word twice is one condition asked twice, and a coverage
    denominator counted once too often."""
    assert _trope_tokens("epilogue what epilogue drarry") == ["epilogue", "drarry"]


def test_si_oc_resolves_as_a_pair():
    """Two initialisms almost always written together, so the pair is its own
    key and is tried before either half."""
    variants, shorthand = _alias_expand("si oc naruto")
    assert variants == ["self insert naruto"]
    assert shorthand is False        # two characters: widen, but do not rank


# ── The same grammar outside Harry Potter ────────────────────────────────────
#
# Verbatim subject lines from Supernatural Story Finders and Teen Wolf
# Storyfinders on LiveJournal. The point of this block is that NOTHING in the
# framing layer is fandom-specific: the same patterns that read "Looking for an
# old Snarry fanfiction" read these, and only the nouns inside them differ.

def test_finder_community_prefixes():
    assert read_request("SF: Jensen leaves abusive relationship").text == (
        "Jensen leaves abusive relationship")
    assert read_request("Rec Request: Dean/Sam with shared mind").text == (
        "Dean/Sam with shared mind")
    assert read_request("FOUND! Mentally Challenged Sam Fic").text == (
        "Mentally Challenged Sam")


def test_for_is_optional():
    """"Searching fic Sam dean fight" and "Looking specific deleted j2 fic" are
    both real subject lines."""
    assert read_request("Searching fic Sam dean fight").text == "Sam dean fight"
    assert read_request("Looking specific deleted j2 fic").text == "j2"


def test_how_many_answers_is_not_a_search_term():
    """"Looking for two fanfics -protective john" — the count says how many
    answers are wanted and appears in none of them."""
    assert read_request("Looking for two fanfics protective john").text == (
        "protective john")
    assert read_request(
        "Looking for a few fics! Omega!Dean, Whump!Dean, etc"
    ).text == "Omega!Dean, Whump!Dean"


def test_one_is_not_a_count():
    """"one shot", "the one where", "Chapter One" are all content."""
    assert "one" in read_request("looking for a fic with only one bed").text


def test_bare_artefact_noun_goes_in_a_request():
    """"Looking for depressed/tied up Sam story" made every result contain the
    word "story"."""
    assert read_request("Looking for depressed/tied up Sam story").text == (
        "depressed/tied up Sam")


def test_bare_artefact_noun_stays_outside_one():
    assert read_request("toy story").text == "toy story"
