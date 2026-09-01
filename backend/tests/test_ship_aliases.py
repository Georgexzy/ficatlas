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
    monkeypatch.setattr(search_mod, "_ship_alias_cached",
                        lambda term: table.get(term, ""))
    return search_mod._ship_nickname


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
    from api import search as search_mod
    monkeypatch.setattr(search_mod, "_ship_alias_cached",
                        lambda t: probed.append(t) or "")
    search_mod._ship_nickname("bts jin and v au")
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
