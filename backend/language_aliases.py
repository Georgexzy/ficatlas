"""Language names, as the different archives actually spell them.

The same language is stored under several names because AO3 records a work's
language in that language, while FF.net and the older dumps use the English
name. Measured on this index:

    中文-普通话 國語   539,198      Chinese         740
    Русский           198,547      Russian       3,180
    Español           118,264      Spanish     337,623
    Français           44,143      French      155,336
    日本語              2,730      Japanese        409

So filtering "Chinese" returned 740 works out of ~546,000, and "Japanese" 409
out of ~3,100 — near enough to nothing, which is exactly how it looked.

Matching any spelling of a language against all of them fixes that. Values here
are the ones present in the data, not a general ISO list; unknown input falls
through to a plain match so a language nobody has tagged still behaves sanely.
"""

# canonical English name -> every spelling seen in the data
LANGUAGE_ALIASES: dict[str, list[str]] = {
    "English":    ["English"],
    "Chinese":    ["Chinese", "中文-普通话 國語", "中文-广东话 粵語", "中文-客家话",
                   "Mandarin", "Cantonese"],
    "Spanish":    ["Spanish", "Español", "Castellano"],
    "Russian":    ["Russian", "Русский"],
    "French":     ["French", "Français"],
    "Portuguese": ["Portuguese", "Português brasileiro", "Português europeu",
                   "Português", "Brazilian Portuguese"],
    "Indonesian": ["Indonesian", "Bahasa Indonesia"],
    "German":     ["German", "Deutsch"],
    "Italian":    ["Italian", "Italiano"],
    "Ukrainian":  ["Ukrainian", "Українська"],
    "Polish":     ["Polish", "Polski"],
    "Filipino":   ["Filipino", "Tagalog"],
    "Vietnamese": ["Vietnamese", "Tiếng Việt"],
    "Czech":      ["Czech", "Čeština"],
    "Turkish":    ["Turkish", "Türkçe"],
    "Japanese":   ["Japanese", "日本語"],
    "Hungarian":  ["Hungarian", "Magyar"],
    "Korean":     ["Korean", "한국어"],
    "Thai":       ["Thai", "ไทย"],
    "Swedish":    ["Swedish", "Svenska"],
    "Finnish":    ["Finnish", "suomi", "Suomi"],
    "Dutch":      ["Dutch", "Nederlands"],
    "Norwegian":  ["Norwegian", "Norsk"],
    "Danish":     ["Danish", "Dansk"],
    "Belarusian": ["Belarusian", "беларуская"],
    "Hebrew":     ["Hebrew", "עברית"],
    "Esperanto":  ["Esperanto"],
    "Arabic":     ["Arabic", "العربية"],
    "Greek":      ["Greek", "Ελληνικά"],
    "Romanian":   ["Romanian", "Română"],
    "Bulgarian":  ["Bulgarian", "български"],
    "Croatian":   ["Croatian", "Hrvatski"],
    "Serbian":    ["Serbian", "Српски"],
    "Catalan":    ["Catalan", "Català"],
    "Latin":      ["Latin", "Latina"],
    "Persian":    ["Persian", "Farsi", "فارسی"],
    "Hindi":      ["Hindi", "हिन्दी"],
}

# lowercase spelling -> canonical name
_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _names in LANGUAGE_ALIASES.items():
    for _n in _names:
        _ALIAS_TO_CANON.setdefault(_n.strip().lower(), _canon)


def language_variants(value: str) -> list[str]:
    """Every stored spelling of a language.

    Returns [] when the name isn't one we know, so the caller can fall back to
    matching the text as given rather than silently matching nothing.
    """
    canon = _ALIAS_TO_CANON.get((value or "").strip().lower())
    if not canon:
        return []
    return LANGUAGE_ALIASES[canon]
