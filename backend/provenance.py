"""Provenance tags — which import a story came from, as opposed to what it's about.

These live in the same `tags` array as real content tags, because that is how the
importers have always written them. That conflation is a problem:

  * 61% of the index (11.6M works) carries ONLY provenance tags, so those stories
    look tagged when nothing is actually known about their content. FF.net is
    100% provenance-only — the metadata dump it comes from has no tag data at all
    (its columns are source_file, category, rating, chapters, words, story_url,
    summary, language), so there is nothing to recover.
  * They swamped tag autocomplete. Typing "dump" suggested `ffnet_dump`
    (3,397,583 uses) and `ao3_meta_dump` (819,964) ahead of the real tag
    "Infodumping" (9); "meta" suggested them ahead of "Meta" (1,081).

They are still worth keeping and still filterable — "show me the DLP-curated
picks" is a real query — but they are not content tags and must not be presented
or suggested as though they were.
"""

# Written by the bulk importers to record where a row came from.
PROVENANCE_TAGS: frozenset[str] = frozenset({
    "ffnet_dump",
    "hf_meta_2024",
    "ao3_meta_dump",
    "janelleshane_seed",
    "hpffa_archive",
    "hexfiles_archive",
    "squidgeworld_archive",
    "dlp_library",
})

# Human-readable source labels for the UI.
PROVENANCE_LABELS: dict[str, str] = {
    "ffnet_dump":           "FF.net dump",
    "hf_meta_2024":         "FF.net dump",
    "ao3_meta_dump":        "AO3 metadata dump",
    "janelleshane_seed":    "HP metadata seed",
    "hpffa_archive":        "HPFFA archive",
    "hexfiles_archive":     "HexFiles archive",
    "squidgeworld_archive": "SquidgeWorld",
    "dlp_library":          "DLP curated",
}


def split_tags(tags: list[str] | None) -> tuple[list[str], list[str]]:
    """Split a stored tag array into (content_tags, provenance_tags)."""
    if not tags:
        return [], []
    content, provenance = [], []
    for t in tags:
        (provenance if t in PROVENANCE_TAGS else content).append(t)
    return content, provenance


def content_tags(tags: list[str] | None) -> list[str]:
    return split_tags(tags)[0]


def source_labels(tags: list[str] | None) -> list[str]:
    """Deduplicated, human-readable sources for a story (order preserved)."""
    seen, out = set(), []
    for t in split_tags(tags)[1]:
        label = PROVENANCE_LABELS.get(t, t)
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out
