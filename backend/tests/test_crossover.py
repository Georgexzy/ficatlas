"""A crossover is more than one FRANCHISE, not more than one fandom tag.

Reported from the live site: the real TWD fic-finder post returned six
crossovers in its first six results — Avengers, Supernatural, Lord of the
Rings, Teen Wolf, Resident Evil — and a twenty-one-fandom SI collection in
which The Walking Dead is one entry. That is arithmetic, not bad luck: a
crossover carries several fandoms, so it matches several fandom searches and
draws readers from all of them, and on a popularity sort it outranks a work
written for the fandom that was actually asked for.

The obvious fix — hide crossovers — could not be trusted, because the flag was
`len(fandoms) > 1` written out in five separate places and AO3 fandom tags are
not franchises. An author files one story under every spelling that fits.
Measured over a 20,000-work sample of flagged AO3 works, 20.6% carry only one
franchise.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crossover import franchise, is_crossover


@pytest.mark.parametrize("fandoms", [
    # Every spelling of one franchise an author might file under.
    ["Star Wars - All Media Types", "Star Wars: The Clone Wars (2008)",
     "Star Wars: Rebels"],
    ["Percy Jackson and the Olympians - Rick Riordan",
     "Percy Jackson and the Olympians & Related Fandoms - All Media Types"],
    ["The Walking Dead (TV)", "The Walking Dead (Comics)"],
    ["Dragon Age - All Media Types", "Dragon Age: Inquisition"],
    ["Harry Potter - J. K. Rowling", "Harry Potter (Movies)"],
    ["原神 | Genshin Impact (Video Game)", "Genshin Impact (Video Game)"],
])
def test_one_franchise_under_several_names_is_not_a_crossover(fandoms):
    assert is_crossover(fandoms) is False


@pytest.mark.parametrize("fandoms", [
    ["The Walking Dead (TV)", "Supernatural (TV 2005)"],
    ["The Walking Dead (TV)", "The Walking Dead (Telltale Video Game)",
     "Resident Evil"],
    ["原神 | Genshin Impact (Video Game)", "Naruto (Anime & Manga)"],
    # An ampersand is ordinary inside a real title, so the umbrella suffix is
    # stripped BY NAME. Cutting at any "&" would merge these two.
    ["Tom & Jerry", "Dungeons & Dragons"],
])
def test_genuinely_different_franchises_are_a_crossover(fandoms):
    assert is_crossover(fandoms) is True


def test_a_single_fandom_is_never_a_crossover():
    assert is_crossover(["The Walking Dead (TV)"]) is False
    assert is_crossover([]) is False
    assert is_crossover(None) is False


def test_the_known_collision_is_recorded_not_hidden():
    """`Avatar: The Last Airbender` and `Avatar (Cameron Movies)` both reduce
    to `avatar`, so a genuine crossover between them reads as one franchise.

    Asserted rather than fixed, because that direction is the SAFE one — a
    crossover kept is a work the reader can see and judge, while a work wrongly
    hidden is invisible — and it is rarer than the false positives the subtitle
    rule removes. If this ever stops being true, this test says so."""
    assert franchise("Avatar: The Last Airbender (Cartoon 2005)") == "avatar"
    assert franchise("Avatar (Cameron Movies)") == "avatar"


def test_one_definition_not_five():
    """It was `len(fandoms) > 1`, written out in ao3_meta_importer,
    huggingface_meta_importer, live_fetch/persist, live_fetch/crosspost and
    crawlers/ao3 — five chances for the rule to drift, which is the same
    argument `is_bot` having one home already settles."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("ao3_meta_importer.py", "huggingface_meta_importer.py",
                "live_fetch/persist.py", "live_fetch/crosspost.py",
                "crawlers/ao3.py"):
        src = (root / rel).read_text()
        assert "fic_is_crossover" in src, rel
        assert "fandoms) > 1" not in src, rel
