"""Canonical character names and the aliases each source actually writes.

The index draws from archives that use incompatible vocabularies for the same
people. FictionAlley (~30k stories) uses terse codes; AO3 uses full canonical
tags. So the same pairing appears as all of:

    D/Hr        Hr/D
    Draco Malfoy/Hermione Granger
    Hermione Granger/Draco Malfoy

A reader typing the obvious "Draco/Hermione" matched none of them, which is the
main reason character and relationship filters appeared not to work.

Two rules matter for matching:

  * Aliases are matched as WHOLE array elements, never as substrings. Several
    codes are a single letter ("H", "D", "R", "G"), and a substring match on
    those would hit essentially every story in the index.
  * Relationships are matched in both orders, since neither archive is
    consistent about which character it lists first.

This covers Harry Potter, which is the overwhelming majority of the rows that
carry any character data at all. Unknown names fall through to the previous
substring behaviour, so other fandoms are unaffected.
"""

# canonical name -> every form seen in the data (plus obvious things a user types)
CHARACTER_ALIASES: dict[str, list[str]] = {
    "Harry Potter":        ["H", "Harry", "Harry Potter"],
    "Hermione Granger":    ["Hr", "Hermione", "Hermione Granger"],
    "Ron Weasley":         ["R", "Ron", "Ron Weasley"],
    "Draco Malfoy":        ["D", "Draco", "Draco Malfoy"],
    "Ginny Weasley":       ["G", "Ginny", "Ginny Weasley"],
    "Severus Snape":       ["Snape", "SS", "Severus", "Severus Snape"],
    "Remus Lupin":         ["RL", "Remus", "Lupin", "Remus Lupin"],
    "Sirius Black":        ["SB", "Sirius", "Sirius Black"],
    "James Potter":        ["JP", "James", "James Potter"],
    "Lily Evans Potter":   ["Lily", "Lily Evans", "Lily Potter", "Lily Evans Potter"],
    "Luna Lovegood":       ["Luna", "Luna Lovegood"],
    "Neville Longbottom":  ["Nev", "Neville", "Neville Longbottom"],
    "Tom Riddle":          ["Tom", "Tom Riddle", "TR"],
    "Voldemort":           ["Vold", "Voldemort", "LV"],
    "Albus Dumbledore":    ["Dum", "Dumbledore", "AD", "Albus Dumbledore"],
    "Lucius Malfoy":       ["LucM", "Lucius", "Lucius Malfoy"],
    "Narcissa Malfoy":     ["NarM", "Narcissa", "Narcissa Malfoy"],
    "Nymphadora Tonks":    ["Tonks", "NT", "Nymphadora Tonks"],
    "Blaise Zabini":       ["BZ", "Blaise", "Blaise Zabini"],
    "Pansy Parkinson":     ["Pansy", "PP", "Pansy Parkinson"],
    "Peter Pettigrew":     ["Peter", "PP2", "Peter Pettigrew"],
    "Bill Weasley":        ["BW", "Bill", "Bill Weasley"],
    "Charlie Weasley":     ["CW", "Charlie", "Charlie Weasley"],
    "Fred Weasley":        ["FW", "Fred", "Fred Weasley"],
    "George Weasley":      ["GW", "George", "George Weasley"],
    "Percy Weasley":       ["PW", "Percy", "Percy Weasley"],
    "Molly Weasley":       ["MW", "Molly", "Molly Weasley"],
    "Arthur Weasley":      ["AW", "Arthur", "Arthur Weasley"],
    "Fleur Delacour":      ["Fleur", "Fleur Delacour"],
    "Cho Chang":           ["Cho", "Cho Chang"],
    "Cedric Diggory":      ["Cedric", "Cedric Diggory", "CD"],
    "Minerva McGonagall":  ["MM", "McGonagall", "Minerva", "Minerva McGonagall"],
    "Bellatrix Lestrange": ["Bella", "Bellatrix", "Bellatrix Lestrange"],
    "Regulus Black":       ["RB", "Regulus", "Regulus Black"],
    "Angelina Johnson":    ["AnJ", "Angelina", "Angelina Johnson"],
    "Oliver Wood":         ["OW", "Oliver", "Oliver Wood"],
    "Seamus Finnigan":     ["Seamus", "Seamus Finnigan"],
    "Dean Thomas":         ["Dean", "Dean Thomas"],
    "Rubeus Hagrid":       ["Hagrid", "Rubeus Hagrid"],
}

# Separators archives use between the two halves of a pairing. The distinction
# matters: AO3 uses "/" for a romantic pairing and "&" for a platonic one, so a
# search for "Draco/Hermione" must not return stories tagged "Draco & Hermione".
ROMANTIC_SEPARATORS = ("/", " x ")
PLATONIC_SEPARATORS = (" & ", "&")
SHIP_SEPARATORS = (*ROMANTIC_SEPARATORS, *PLATONIC_SEPARATORS)

# lowercase alias -> canonical name
_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in CHARACTER_ALIASES.items():
    _ALIAS_TO_CANON[_canon.lower()] = _canon
    for _a in _aliases:
        _ALIAS_TO_CANON.setdefault(_a.lower(), _canon)


def resolve_character(value: str) -> str | None:
    """Canonical name for whatever the user typed, or None if we don't know it."""
    return _ALIAS_TO_CANON.get(value.strip().lower())


def character_variants(value: str) -> list[str]:
    """Every stored spelling of a character. Empty when the name is unknown, which
    signals the caller to fall back to substring matching."""
    canon = resolve_character(value)
    if not canon:
        return []
    return sorted({canon, *CHARACTER_ALIASES[canon]})


def _split_ship(value: str) -> tuple[str, str, bool] | None:
    """(left, right, is_platonic), or None if this isn't a pairing.

    Platonic separators are checked first: " & " would otherwise never be reached
    for a value that also contains no "/", and we need to know which kind of
    pairing the reader asked for.
    """
    for sep in (*PLATONIC_SEPARATORS, *ROMANTIC_SEPARATORS):
        if sep in value:
            left, _, right = value.partition(sep)
            if left.strip() and right.strip():
                return left.strip(), right.strip(), sep in PLATONIC_SEPARATORS
    return None


def relationship_variants(value: str) -> list[str]:
    """Every stored spelling of a pairing, in both orders.

    Only expands within the kind of pairing that was asked for — a romantic
    "Draco/Hermione" never expands to the platonic "Draco & Hermione".

    Returns [] when either half is a character we have no aliases for, so the
    caller keeps its existing substring behaviour rather than silently matching
    nothing.
    """
    split = _split_ship(value)
    if not split:
        return []
    left, right, is_platonic = split
    left_vars = character_variants(left)
    right_vars = character_variants(right)
    if not left_vars or not right_vars:
        return []

    separators = PLATONIC_SEPARATORS if is_platonic else ROMANTIC_SEPARATORS
    out = set()
    for a in left_vars:
        for b in right_vars:
            for sep in separators:
                out.add(f"{a}{sep}{b}")
                out.add(f"{b}{sep}{a}")
    return sorted(out)
