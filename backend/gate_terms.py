"""The terms the site will not put in front of somebody who did not ask.

THE ONE PLACE THESE LISTS LIVE. They were written out twice — here in
`content_gates.py`, which owns the database trigger, and again in
`api/search.py`, which owns the query filters — and they drifted, in the
direction that matters:

    UNDERAGE_TAGS   content_gates 31   api/search 16   (15 missing)
    ADULT_TAGS      content_gates 60   api/search 29   (31 missing)

`api/search.py` was the laxer copy, and it was missing the most serious terms
in the set: `Child Sexual Abuse`, `Child Grooming`, `Statutory Rape`,
`Adult/Minor Relationship`, `Ephebophilia`, `Dubious Consent`, `Family
Incest`. The gate COLUMNS were still computed from the fuller list here, so the
trigger and the backfill were right — but every belt-and-braces array check in
the search path, which exists precisely for rows the backfill has not reached,
was checking the short list.

CLAUDE.md had already written down the rule this broke: "two lists of what
counts as this content would drift, and the one that drifts laxer is the bug."
It said so about `api/hubs.py`, which does import rather than copy. The copy
nobody noticed was one level up.

This module holds the lists and NOTHING else — no database, no imports, no
side effects — so anything can import it without dragging a connection or an
environment variable along.
"""

UNDERAGE_WARNINGS = ["Underage Sex", "Underage"]

UNDERAGE_TAGS = [
    "Underage Sex - Freeform", "Consensual Underage Sex",
    "Underage Rape/Non-con", "Implied/Referenced Underage Sex",
    "Underage - Freeform", "Extremely Underage", "Underage",
    "Underage Sex", "Underage Sexual Activity", "Underage Masturbation",
    "Underage Prostitution", "Underage Pregnancy", "Underage Smut",
    "Minor/Adult Relationship", "Adult/Minor Relationship",
    "Teenage Sexuality", "Underage Drinking and Sex",
    "Pedophilia", "Implied/Referenced Pedophilia", "Pedophile",
    "Grooming", "Child Grooming", "Child Abuse - Sexual",
    "Child Sexual Abuse", "Childhood Sexual Abuse",
    "Shotacon", "Lolicon", "Chan", "Ephebophilia",
    "Statutory Rape", "Underage Non-Consensual",
]

# ── Tier 2: explicit sex, and the deliberately disturbing ───────────────────
ADULT_WARNINGS = ["Rape/Non-Con", "Rape/Non-con"]

ADULT_TAGS = [
    # Explicit sexual content, by its usual names.
    "Smut", "PWP", "Plot What Plot/Porn Without Plot", "Porn With Plot",
    "Porn with Feelings", "Porn", "Pornography", "Explicit Sexual Content",
    "Graphic Depictions of Sex", "Explicit Language and Sexual Content",
    "Rough Sex", "Anal Sex", "Oral Sex", "Vaginal Sex", "Threesome - M/M/F",
    "Threesome - F/F/M", "Orgy", "Gangbang", "Sex Toys", "BDSM",
    "Dubious Consent", "Dub-Con", "Dubcon",
    # AO3's own marker for "this is as unpleasant as it says on the tin".
    "Dead Dove: Do Not Eat", "Dead Dove Do Not Eat",
    # Non-consent.
    "Rape/Non-con Elements", "Rape", "Non-Con", "Noncon", "Non-con",
    "Implied/Referenced Rape/Non-con", "Past Rape/Non-con",
    "Attempted Rape/Non-Con", "Rape/Non-con", "Rape Aftermath",
    "Non-Consensual", "Forced Orgasm", "Sexual Assault",
    "Implied/Referenced Sexual Assault",
    # Incest.
    "Incest", "Sibling Incest", "Parent/Child Incest",
    "Brother/Brother Incest", "Brother/Sister Incest",
    "Sister/Sister Incest", "Twincest", "Implied/Referenced Incest",
    "Parent/Child Relationship", "Family Incest",
    # The rest of the obvious.
    "Bestiality", "Necrophilia", "Cannibalism", "Snuff",
    "Torture", "Graphic Torture", "Mutilation", "Self-Harm",
    "Suicide", "Suicidal Thoughts", "Eating Disorders",
]
