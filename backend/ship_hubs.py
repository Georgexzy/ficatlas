"""Crawlable entry points into the index, one per pairing.

Why this exists
---------------
The fandom hubs (fandom_hubs.py) gave a crawler a bounded way in, but they
compete for the one query FicAtlas cannot win: nobody outranks AO3 for
"[fandom] fanfiction", because AO3 *is* the answer to it.

Ships are the long tail underneath that, and they are how people actually search
for fic — by pairing, not by fandom. The demand is real and it is concentrated:
"Castiel/Dean Winchester" carries 83,582 works in this index, "Draco
Malfoy/Harry Potter" 47,460. There are 575,979 distinct relationship facets, of
which 3,256 carry 500 works or more.

It is also where the cross-archive claim is provable rather than asserted. AO3's
own tag pages cover AO3; a ship hub here lists the FanFiction.net and FictionAlley
works for the same pairing next to them, which no single archive can do.

Collapsing pairing order
------------------------
Archives disagree about which half of a pairing goes first, and the same ship
arrives as several facet rows:

    Draco Malfoy/Harry Potter     47,460
    Harry Potter/Draco Malfoy      1,541
    Harry Potter/ Draco Malfoy        46

Three hubs for one ship would be duplicate content pointing at overlapping work,
and would split 49,047 works across a strong page and two thin ones. ship_key()
sorts the halves into a canonical order so all three collapse together, the same
job fandom_base() does for author suffixes.

relationship_variants() in character_aliases.py already does this and better —
it expands nicknames and archive codes too — but only for the ~40 Harry Potter
characters it has aliases for, returning [] for everything else. Hubs need a
rule that holds for all 575,979 facet rows, so the ordering normalisation here
is deliberately dumber and total. The two are complementary: this decides which
facet rows share a page, the alias expansion decides what a reader's typed
filter matches.

Romantic pairings only
----------------------
AO3 writes "/" for a romantic pairing and " & " for a platonic one, and the
distinction is not cosmetic to the people reading — "Draco Malfoy & Harry
Potter" (2,939 works) is a different thing from the ship, and readers of one are
frequently not looking for the other.

Slugs cannot tell them apart: both slugify to `draco-malfoy-harry-potter`, so
building both kinds would silently merge them onto one URL. Rather than invent a
prefix nobody would recognise, this builds romantic pairings only. Platonic hubs
can be added later under their own path if they earn it; merging them is the one
option that is simply wrong.
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from character_aliases import ROMANTIC_SEPARATORS, PLATONIC_SEPARATORS
from fandom_hubs import slugify
from hub_build import build_groups

log = logging.getLogger(__name__)

# How many works a hub links to PER SITE. Matches the fandom hubs, for the same
# reasons — see WORKS_PER_SITE there.
WORKS_PER_SITE = 50

# Minimum works for a pairing to get a hub. Measured on this index:
#
#     >=  200 works   8,594 pairings
#     >=  500 works   3,256 pairings
#     >= 1000 works   1,451 pairings
#
# 500 kept the new crawlable surface to roughly the size of the existing fandom
# set (5,025) rather than doubling it in one step. The note that used to sit here
# said this was "a dial, not a law: lowering it is safe once the hubs are being
# crawled at all, which is currently unknown."
#
# It is no longer unknown, so the dial has moved to 200. The evidence, from this
# site's own traffic table:
#
#   * search engines are crawling. Bot hits per day went 1 (24 Aug) -> 39 -> 126
#     (26 Aug), after the hub URLs were submitted through IndexNow.
#   * ship hubs are what RANKS. Of the organic referrals recorded, five of seven
#     Google landings were /ship/ pages and one Yandex landing was /fandom/ —
#     no other page type has brought a visitor in.
#   * and they rank in the LONG TAIL, not the famous fandoms: Papa Emeritus
#     III/Reader, Kim Seokjin/Park Jimin, Charlie Magne/Vaggie, Kim
#     Seungmin/Yang Jeongin. Exactly where a cross-archive index has something
#     to say and AO3's own tag pages compete least.
#
# 200 adds ~3,965 pairings to the ~2,768 already over 500. It is deliberately not
# 100 (a further 5,740) — one measured step, so the effect on crawl rate and
# ranking can be read before taking another. A pairing with 200 works still fills
# the page, which caps at WORKS_PER_SITE per archive, so nothing thin is created.
MIN_WORKS_FOR_HUB = 200

# Fandom names for a pairing, keyed by slug.
#
# Nobody searches "Draco Malfoy/Harry Potter fanfiction". They search "drarry".
# The portmanteau is the primary name a ship has inside fandom and the canonical
# tag is the formal one, and until this existed a ship hub contained the formal
# name and nothing else — so the page for 49,964 Drarry works could not match
# the word Drarry, on the page or in a search engine.
#
# Curated rather than derived. A portmanteau is not a function of the two names
# (Harry + Hermione is "Harmony", Sirius + Remus is "Wolfstar"), and a wrong one
# is worse than none: these are the words readers identify a ship by, so getting
# one wrong is immediately visible to exactly the audience this is for. Every
# slug below was checked against the built ship_hubs table rather than guessed —
# the first pass had Steve/Eddie and Katara/Zuko under the wrong slug, because
# the slug is alphabetical and the tag is not.
#
# Serving-time rather than a column: the list is static, editing it takes effect
# without a rebuild of 2,553 rows, and it needs no DDL.
#
# First entry is the primary; the rest are accepted spellings.
SHIP_NICKNAMES: dict[str, list[str]] = {
    "draco-malfoy-harry-potter":        ["Drarry"],
    "draco-malfoy-hermione-granger":    ["Dramione"],
    "harry-potter-hermione-granger":    ["Harmony", "Harmione"],
    "hermione-granger-ron-weasley":     ["Romione"],
    "ginny-weasley-harry-potter":       ["Hinny"],
    "remus-lupin-sirius-black":         ["Wolfstar"],
    "james-potter-lily-evans-potter":   ["Jily"],
    "castiel-dean-winchester":          ["Destiel"],
    "dean-winchester-sam-winchester":   ["Wincest"],
    "john-watson-sherlock-holmes":      ["Johnlock"],
    "derek-hale-stiles-stilinski":      ["Sterek"],
    "james-bucky-barnes-steve-rogers":  ["Stucky"],
    "steve-rogers-tony-stark":          ["Stony"],
    "harry-styles-louis-tomlinson":     ["Larry Stylinson", "Larry"],
    "keith-lance-voltron":              ["Klance"],
    "bakugou-katsuki-midoriya-izuku":   ["Bakudeku", "KatsuDeku"],
    "eddie-munson-steve-harrington":    ["Steddie"],
    "aziraphale-crowley-good-omens":    ["Ineffable Husbands"],
    "dazai-osamu-nakahara-chuuya-bungou-stray-dogs": ["Soukoku"],
    "alec-lightwood-magnus-bane":       ["Malec"],
    "kara-danvers-lena-luthor":         ["Supercorp"],
    "clarke-griffin-lexa":              ["Clexa"],
    "bilbo-baggins-thorin-oakenshield": ["Bagginshield"],
    "annabeth-chase-percy-jackson":     ["Percabeth"],
    "katniss-everdeen-peeta-mellark":   ["Everlark"],
    "james-t-kirk-spock":               ["Spirk"],
    "katara-zuko-avatar":               ["Zutara"],
    "buffy-summers-spike":              ["Spuffy"],
    "ben-solo-kylo-ren-rey":            ["Reylo"],
    "kylo-ren-rey":                     ["Reylo"],
    "simon-snow-tyrannus-basilton-baz-pitch": ["Snowbaz"],
    "alexander-hamilton-john-laurens":  ["Lams"],
}


def nicknames_for(slug: str) -> list[str]:
    """Fandom names for a pairing. Empty for the ones that have none."""
    return SHIP_NICKNAMES.get(slug, [])


# A pairing with more halves than this gets no hub. Threesomes are a real
# category with real readership; beyond that the tags are long, rare, and
# produce slugs nobody would ever type or link.
MAX_PARTS = 3


def _parts(value: str) -> list[str] | None:
    """The halves of a romantic pairing, cleaned, in the order they were written.

    None for anything that is not one. Both ship_key and ship_display are built
    from this, so the rule for what counts as a pairing is stated once.
    """
    raw = (value or "").strip()
    if not raw:
        return None

    # A value carrying both kinds of separator ("Draco/Harry & Ron Weasley") is
    # a romantic pairing plus a platonic one and belongs cleanly to neither
    # page, so it is skipped rather than guessed at.
    #
    # This also rejects a pairing whose fandom qualifier happens to contain an
    # ampersand ("A/B (Dungeons & Dragons)"). That is a false negative and it is
    # the right way round to be wrong: the cost is one hub not built, where
    # guessing costs a page that merges a ship with a friendship.
    if any(sep in raw for sep in PLATONIC_SEPARATORS):
        return None

    if not any(sep in raw for sep in ROMANTIC_SEPARATORS):
        return None

    # Split on every romantic separator, not just the first: "A/B/C" is a
    # three-way pairing, and partitioning once would read it as "A" with "B/C".
    parts = [raw]
    for sep in ROMANTIC_SEPARATORS:
        parts = [p for chunk in parts for p in chunk.split(sep)]

    # " ".join(split()) collapses the internal double space that makes
    # "Harry  Potter" a separate facet row from the same character. Stripping
    # each part is what removes the leading space in "Harry Potter/ Draco
    # Malfoy" — 46 works tagged that way, and rejoining below is what stops it
    # reaching the page heading and the search link.
    parts = [" ".join(p.split()) for p in parts]
    if not all(parts) or not 2 <= len(parts) <= MAX_PARTS:
        return None
    return parts


def ship_display(value: str) -> str | None:
    """The pairing as written, with separators and spacing normalised.

    Order is preserved, unlike ship_key: this is what a reader sees and what the
    page's search link carries, and both want the spelling the archives actually
    use rather than an alphabetised one.
    """
    parts = _parts(value)
    return "/".join(parts) if parts else None


def ship_key(value: str) -> str | None:
    """The slug a romantic pairing belongs to, or None if it isn't one.

    Alphabetical, so "A/B" and "B/A" produce the same slug. Deliberately NOT
    derived from whichever spelling is most popular, even though that is what
    the page displays: popularity moves between rebuilds and an order derived
    from it would silently rename URLs that are already indexed and linked.
    Alphabetical order cannot change.

    Returns None for anything without a romantic separator, which is what keeps
    non-pairings out: "Minor or Background Relationship(s)" is the third largest
    relationship facet in the index at 49,223 works and is not a ship.
    """
    parts = _parts(value)
    if not parts:
        return None
    # Case-insensitive ordering, so "harry potter/Draco Malfoy" lands on the
    # same slug as the properly-cased row.
    return slugify("/".join(sorted(parts, key=str.casefold))) or None


def _collapse(rows: Iterable[tuple[str, int]]) -> dict[str, dict]:
    """Group facet rows into one entry per pairing, keyed by slug.

    The display name is the MOST USED spelling, not the alphabetical one the
    slug is built from. Both halves of that matter:

      * Readers know a ship by its usual name. Sorting "Sherlock Holmes/John
        Watson" (60,810 works) into "John Watson/Sherlock Holmes" would put a
        spelling on the page that almost nobody writes.
      * The page's search link passes the display name through as a filter, and
        search resolves a facet term by substring against the vocabulary. Handing
        it the rare spelling would resolve against the 1,541-work variant while
        the 47,460-work one sat next to it.

    `approx` sums counts across variants only as an ordering hint for which hubs
    matter most. It overstates the true total, because a work tagged both "A/B"
    and "B/A" is counted twice. The figure shown to a reader is the exact one
    recomputed by the builder; this is not that number and is never shown.
    """
    hubs: dict[str, dict] = {}
    best: dict[str, tuple[int, str]] = {}
    for value, count in rows:
        slug = ship_key(value)
        if not slug:
            continue
        display = ship_display(value)
        if not display:
            continue
        hub = hubs.setdefault(slug, {"name": display, "variants": [], "approx": 0})
        hub["variants"].append(value)
        hub["approx"] += count
        # Ranked on (count, spelling), so two variants with identical counts
        # resolve on the name rather than on which row the database handed back
        # first. Comparing on count alone left the display name — and therefore
        # the page heading and its search link — dependent on row order.
        rank = (count, display)
        if rank > best.get(slug, (-1, "")):
            best[slug] = rank
            hub["name"] = display
    return hubs


def build_ship_hubs(db: Session, min_works: int = MIN_WORKS_FOR_HUB,
                    per_hub: int = WORKS_PER_SITE,
                    limit: int | None = None) -> int:
    """Rebuild ship_hubs from the facets table. Returns the number written."""
    rows = db.execute(text(
        "SELECT value, count FROM facets "
        " WHERE kind = 'relationship' AND count >= :m"
    ), {"m": min_works}).fetchall()
    hubs = _collapse((r[0], r[1]) for r in rows)

    if limit:
        hubs = dict(sorted(hubs.items(), key=lambda kv: -kv[1]["approx"])[:limit])

    # See the note in fandom_hubs.build_hubs: pruning a partial run deletes
    # every hub it did not rebuild.
    return build_groups(db, table="ship_hubs", array_col="relationships",
                        groups=hubs, per_hub=per_hub, prune=not limit)


if __name__ == "__main__":
    import argparse
    from db.session import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Rebuild ship hub pages.")
    ap.add_argument("--min-works", type=int, default=MIN_WORKS_FOR_HUB)
    ap.add_argument("--per-site", type=int, default=WORKS_PER_SITE,
                    dest="per_hub", help="Top works kept per archive.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only build the N largest pairings (for a trial run). "
                         "Skips the stale sweep, so it never deletes hubs it "
                         "did not rebuild.")
    args = ap.parse_args()

    with SessionLocal() as s:
        n = build_ship_hubs(s, args.min_works, args.per_hub, args.limit)
    log.info("wrote %d ship hubs", n)
