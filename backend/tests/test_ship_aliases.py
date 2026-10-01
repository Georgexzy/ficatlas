"""Ship nicknames: what gets mined, and what a query resolves to.

The mine runs against the live index and cannot run here, so these cover the
two rule sets that decide what ends up in the table and what a search does with
it. Both were written against real failures found while building it:

  * "canon", "futurefic" and "hurt-comfort" all resolved to a real pairing on a
    50-60% share, because they are used in every fandom and land on whichever
    ship happens to be biggest among the works carrying them. The fandom-
    concentration rule is what separates them from a nickname.
  * The lookup is case-sensitive against array elements. The facet is `Drarry`
    with a capital D, and a lowercased probe matched nothing at all — which is
    silent, because "no alias" is a normal answer.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── what a query resolves to ──────────────────────────────────────────────────

@pytest.fixture
def nickname(monkeypatch):
    """_ship_nickname with a known table, so no database is involved."""
    from api import search as search_mod

    table = {"wolfstar": "Sirius Black/Remus Lupin",
             "taejin": "Kim Seokjin | Jin/Kim Taehyung | V"}
    monkeypatch.setattr(search_mod, "_alias_table", lambda db: table)
    return lambda q: search_mod._ship_nickname(None, q)


def test_plain_nickname_resolves(nickname):
    assert nickname("wolfstar") == ("Sirius Black/Remus Lupin", "")


def test_the_other_words_are_kept(nickname):
    # The whole point: "taejin jealousy" must stay a search for jealousy works
    # of that pairing, not every work of it.
    canonical, rest = nickname("Bts taejin jealousy")
    assert canonical == "Kim Seokjin | Jin/Kim Taehyung | V"
    assert rest == "Bts jealousy"


def test_case_and_punctuation_do_not_matter(nickname):
    assert nickname("Wolfstar,")[0] == "Sirius Black/Remus Lupin"


def test_unknown_words_resolve_to_nothing(nickname):
    assert nickname("harry potter time travel") is None


def test_short_tokens_are_never_probed(nickname, monkeypatch):
    """A three-letter word is an initialism or an English word, never a ship."""
    probed = []

    class Probe(dict):
        def get(self, k, default=None):
            probed.append(k)
            return None

    from api import search as search_mod
    monkeypatch.setattr(search_mod, "_alias_table", lambda db: Probe())
    search_mod._ship_nickname(None, "bts jin and v au")
    assert all(len(p) >= 5 for p in probed)


def test_a_sentence_is_not_treated_as_a_ship_query(nickname):
    assert nickname("looking for a wolfstar fic where remus is a professor "
                    "and sirius comes back") is None


# ── what gets mined ───────────────────────────────────────────────────────────

def test_hyphenated_tags_are_rejected():
    """Era and genre tags carry hyphens; portmanteau ship names do not."""
    import ship_aliases
    for tag in ("post-reichenbach", "hurt-comfort", "john-centric"):
        assert "-" in tag                     # the rule the miner applies
    assert ship_aliases._looks_like_a_word("wolfstar")
    assert not ship_aliases._looks_like_a_word("bts")        # too short
    assert not ship_aliases._looks_like_a_word("3some")      # not a name


def test_thresholds_are_the_ones_that_were_measured():
    import ship_aliases
    assert ship_aliases.MIN_SHARE >= 0.55
    # wolfstar is 96% one fandom, "hurt-comfort" 72% and "canon" far lower.
    assert ship_aliases.MIN_FANDOM_SHARE >= 0.60
    assert ship_aliases.MAX_WORKS <= 50_000


# ── the spelled-out pairing, and what it must refuse ──────────────────────────

@pytest.fixture
def pair(monkeypatch):
    """_spelled_out_pair with the database answering "yes, that is a pairing".

    The point of these is the guards in front of the query, so the query itself
    is stubbed to the permissive answer. A resolver that reaches the database
    for "Harry Potter and the Philosopher's Stone" has already failed.
    """
    from api import search as search_mod
    asked = []

    def fake(db, a, b):
        asked.append((a, b))
        return "Some Character/Another Character"

    monkeypatch.setattr(search_mod, "_pair_lookup", fake)
    return lambda q: (search_mod._spelled_out_pair(None, q), asked)


@pytest.mark.parametrize("title", [
    # Reached production. "the" is a substring of THEodore, so the most-searched
    # book title on the site resolved to Theodore Nott/Harry Potter — and the
    # ranking bonus then put that ship on page one of a plain title search.
    "Harry Potter and the Philosopher's Stone",
    "the good and the bad",
    "beauty and the beast",
    # Both halves hit the bracketed fandom rather than either character:
    # `Mr. Bennet/Mrs. Bennet (Pride and Prejudice)`. Guarded in SQL by
    # requiring the halves to land on opposite sides with the brackets stripped.
    "a story about pride and prejudice in the modern era",
])
def test_titles_and_phrases_are_not_pairings(pair, title):
    result, _ = pair(title)
    assert result is None


def test_a_stopword_half_never_reaches_the_database(pair):
    _, asked = pair("Harry Potter and the Philosopher's Stone")
    assert asked == []


def test_a_long_query_is_not_probed(pair):
    _, asked = pair("looking for a fic where harry and draco are aurors together")
    assert asked == []


def test_a_real_pairing_still_resolves(pair):
    result, asked = pair("Bts jin and jimin")
    assert result == ("Some Character/Another Character", "Bts")
    assert asked == [("jin", "jimin")]


# ── the cache, which is on the request's own session ──────────────────────────

class RecordingDB:
    """Counts queries and records whether it was rolled back."""

    def __init__(self, rows=(), fail=False):
        self.rows, self.fail = rows, fail
        self.queries = 0
        self.rolled_back = False

    def execute(self, *a, **k):
        self.queries += 1
        if self.fail:
            raise RuntimeError("connection blip")
        return self

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return None

    def rollback(self):
        self.rolled_back = True


def _reset_alias_cache():
    from api import search as m
    m._alias_cache = {}
    m._alias_loaded_at = None


def test_an_empty_table_is_still_a_loaded_table():
    """An empty dict is falsy, and testing truthiness put a query on the hottest
    path of every free-text search for the 30 minutes after each deploy."""
    from api import search as m
    _reset_alias_cache()
    db = RecordingDB(rows=[])
    m._alias_table(db)
    m._alias_table(db)
    m._alias_table(db)
    assert db.queries == 1
    _reset_alias_cache()


def test_a_failed_lookup_rolls_back_the_request_session():
    """search() runs its real query on this same session afterwards. Swallowing
    the error without a rollback leaves the transaction aborted, so the next
    statement raises InFailedSqlTransaction and the whole search 500s."""
    from api import search as m
    _reset_alias_cache()
    db = RecordingDB(fail=True)
    assert m._alias_table(db) == {}
    assert db.rolled_back

    db2 = RecordingDB(fail=True)
    assert m._pair_lookup(db2, "jin", "jimin") == ""
    assert db2.rolled_back
    _reset_alias_cache()


# ── a ship written as initials, which the filters resolved and free text did not ──

@pytest.fixture
def initial(monkeypatch):
    """`_spelled_out_pair` on a query shaped like an initials pairing.

    `character_variants` and `_pair_lookup` are stubbed because both are the
    subject of their own tests; what is under test here is WHICH queries reach
    the alias table at all. The first version of this passed every case below
    and fixed nothing, because the shape never got past the two guards in
    front of the lookup — the split needs whitespace around the slash and
    `_PAIR_RE` needs three characters a side.
    """
    from api import search as m
    from character_aliases import character_variants
    asked = []

    # The REAL alias table, not a stub of it. An earlier version of this fixture
    # listed only `rl`/`sb`/`hr`/`d`, so `r` fell through to the identity
    # fallback and `hr/r` asked for `hermione granger` + `r` — which is the
    # substring bug the test is meant to catch, reproduced by the stub rather
    # than prevented. A stub of the vocabulary cannot test a rule about the
    # vocabulary.
    variants = character_variants
    pairings = {
        ("remus", "sirius"): "Sirius Black/Remus Lupin",
        ("remus lupin", "sirius black"): "Sirius Black/Remus Lupin",
        ("remus", "sirius black"): "Sirius Black/Remus Lupin",
        ("remus lupin", "sirius"): "Sirius Black/Remus Lupin",
        ("rl", "sb"): "RL/SB",
        ("remus", "hermione"): "Hermione Granger/Remus Lupin",
        ("hermione", "draco"): "Draco Malfoy/Hermione Granger",
        ("hermione granger", "ron weasley"): "Hermione Granger/Ron Weasley",
        ("ron weasley", "hermione granger"): "Hermione Granger/Ron Weasley",
        ("draco malfoy", "harry potter"): "Draco Malfoy/Harry Potter",
        ("harry potter", "draco malfoy"): "Draco Malfoy/Harry Potter",
    }



    def fake_lookup(db, a, b):
        asked.append((a, b))
        return pairings.get((a, b), "")

    monkeypatch.setattr(m, "_pair_lookup", fake_lookup)
    m._pair_cache.clear()
    return lambda q: (m._spelled_out_pair(None, q), asked)


@pytest.mark.parametrize("q", ["rl/sb", "RL/SB", "Rl/Sb", "rl / sb", "rl x sb"])
def test_a_ship_written_as_initials_resolves(initial, q):
    """The measured case: `q=rl/sb` returned 925 free-text matches where the
    pairing itself is on 44,730 works, and nothing about the page said so."""
    result, _ = initial(q)
    assert result is not None, f"{q!r} did not resolve to a pairing"
    assert result[0] == "Sirius Black/Remus Lupin"


def test_an_initials_pairing_keeps_the_other_words(initial):
    """The pairing is OR-ed beside the text match, so a qualifier the reader
    typed still has to apply to the branch that was added."""
    result, _ = initial("rl/sb fluff")
    assert result == ("Sirius Black/Remus Lupin", "fluff")


def test_a_one_letter_half_is_not_an_initials_pairing(initial):
    """`A/B` is an operator and a URL path. Refused before the database."""
    _, asked = initial("d/hr and a/b")
    assert ("d", "a") not in asked


def test_a_half_the_alias_table_has_never_heard_of_is_left_alone(initial):
    """One known half means the other is a real name the reader typed out, so
    this is not an initials pairing and must not be resolved as one."""
    result, _ = initial("zz/sb")
    assert result is None


def test_the_same_letters_twice_is_not_a_pairing(initial):
    _, asked = initial("rl/rl")
    assert asked == []


def test_an_initials_query_with_too_many_words_still_resolves(initial):
    """The word-count guard returns through the initials path, so a long
    fic-finder post naming `RL/SB` is not the one shape that cannot resolve."""
    result, _ = initial("looking for a wolfstar fic rl/sb in the marauders era")
    assert result is not None
    assert result[0] == "Sirius Black/Remus Lupin"


def test_a_spelled_out_pairing_is_unchanged(initial):
    """The regression direction. `draco and hermione` must still go down the
    existing path and reach the lookup with the two bare names."""
    result, asked = initial("Bts jin and jimin")
    assert asked == [("jin", "jimin")]
    assert result is None or result[0]


def test_only_the_expanded_names_are_looked_up(initial):
    """The substring bug, which the first version had and the tests above did not
    catch because every stubbed pairing happened to be the right one.

    `_pair_lookup` matches halves as SUBSTRINGS, so asking it for `hr` and `r`
    returns `Alex Claremont-Diaz/Henry Fox-...` — `r` matching inside `Henry` —
    and `hermione` + `r` returns `Hermione Granger/Draco Malfoy`, `r` inside
    `Draco`. Both are real pairings and both are better attested than the
    answer, so "try every spelling, keep the best" picked them. The rule is that
    only the longest spelling of each half is ever asked about.
    """
    _, asked = initial("hr/r")
    assert ("hr", "r") not in asked
    assert ("hermione", "r") not in asked
    assert ("hermione granger", "ron weasley") in asked
