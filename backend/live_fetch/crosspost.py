"""Cross-post detection and merging.

The same fic is often posted on multiple sites (AO3 + FFN, etc). We want one
search result per *work*, not one per site, with links to every copy and the
hosted full text taken from the most recently updated copy.

Matching strategy (conservative — we'd rather miss a match than merge two
different stories):
  - a normalized (title, author) key, OR
  - an explicit cross_post_urls link already recorded on a row.

Normalization lower-cases, strips punctuation/whitespace and common author
suffixes so "Harry's Story" by "Jane_Doe" matches "Harry's Story" by "jane doe".
"""
import re
from datetime import datetime
from collections import defaultdict
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from models.story import Story

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def norm_title(title: str) -> str:
    if not title:
        return ""
    t = _PUNCT.sub(" ", title.lower())
    return _WS.sub(" ", t).strip()


def norm_author(author: str) -> str:
    if not author:
        return ""
    a = author.lower().strip()
    # FFN/AO3 pseudonyms vary: "jane_doe", "jane doe", "Jane-Doe" should all match.
    # Strip everything that isn't a letter or digit (underscores included).
    a = re.sub(r"[^a-z0-9]", "", a)
    return a


# Titles and authors that identify nothing. These are real values in the data, not
# parse failures — AO3 has 58,688 distinct works titled "Unknown" by "Anonymous",
# and hundreds each of "Untitled"/"Home"/"Nightmares" by "orphan_account" (the
# pseudonym AO3 assigns when a user orphans their work). Keying on them would
# merge tens of thousands of unrelated stories into one row.
_PLACEHOLDER_TITLES = {
    "unknown", "untitled", "no title", "none", "tbd", "test", "drabble",
    "drabbles", "oneshot", "one shot", "prologue", "chapter 1", "story",
}
_PLACEHOLDER_AUTHORS = {
    "anonymous", "orphanaccount", "orphan", "unknown", "anon", "guest", "admin",
}

# A work genuinely cross-posted to several archives has a handful of copies. Any
# "group" bigger than this is a collision on a common title, not one work.
MAX_PLAUSIBLE_COPIES = 6


def match_key(title: str, author: str) -> str | None:
    """A conservative identity key for a work. Returns None when too little to
    safely match on (no author, trivially short title, or a placeholder identity
    that would match unrelated works)."""
    nt, na = norm_title(title), norm_author(author)
    if not na or len(nt) < 6:        # need a real author and a non-trivial title
        return None
    if nt in _PLACEHOLDER_TITLES or na in _PLACEHOLDER_AUTHORS:
        return None
    return f"{nt}::{na}"


def _best_updated(stories: list[Story]) -> Story:
    """Pick the copy to treat as canonical hosted source: hosted first, then the
    most recently updated, then the highest word count as a tiebreak."""
    def sort_key(s: Story):
        return (
            1 if s.is_hosted else 0,
            s.updated_at or datetime.min,
            s.word_count or 0,
        )
    return sorted(stories, key=sort_key, reverse=True)[0]


def group_existing(db: Session, limit: int | None = None) -> list[list[Story]]:
    """Scan the DB and return groups of 2+ stories that look like the same work
    across different sites. Single-site duplicates are ignored (those are handled
    by the unique site+site_id constraint)."""
    # Group in SQL, then fetch only the rows that are actually in a group.
    #
    # Doing this in Python meant walking every row and building a dict keyed by
    # (title, author). At 19M works that dict alone is multiple GB, so the batch
    # could only be run with a `limit` — and `limit` bounds the SCAN, not the
    # output, so cross-posted pairs that straddled the cutoff were simply missed:
    # a 400k-row scan found 12 groups where the whole table holds ~83k.
    #
    # Postgres can do the grouping without materialising anything on our side.
    # The normalisation below must stay equivalent to norm_title/norm_author:
    #   title  — lowercase, punctuation to spaces, whitespace collapsed
    #   author — lowercase, everything but [a-z0-9] stripped
    sql = sql_text("""
        WITH normed AS (
            SELECT id, site,
                   btrim(regexp_replace(regexp_replace(lower(title), '[^\\w\\s]', ' ', 'g'),
                                        '\\s+', ' ', 'g')) AS nt,
                   regexp_replace(lower(author), '[^a-z0-9]', '', 'g')  AS na
            FROM stories
            WHERE title IS NOT NULL AND author IS NOT NULL
        )
        SELECT array_agg(id) AS ids
        FROM normed
        WHERE length(nt) >= 6
          AND na <> ''
          AND NOT (nt = ANY(:bad_titles))
          AND NOT (na = ANY(:bad_authors))
        GROUP BY nt, na
        -- 2..max copies spanning at least two sites: a genuine cross-post.
        HAVING count(*) BETWEEN 2 AND :max_copies
           AND count(DISTINCT site) >= 2
        LIMIT :lim
    """)
    id_groups = [row[0] for row in db.execute(sql, {
        "bad_titles":  sorted(_PLACEHOLDER_TITLES),
        "bad_authors": sorted(_PLACEHOLDER_AUTHORS),
        "max_copies":  MAX_PLAUSIBLE_COPIES,
        "lim":         limit or 100000,
    }).fetchall()]
    if not id_groups:
        return []

    # Load only the rows in a group — a few hundred thousand at most, not 19M.
    all_ids = [i for g in id_groups for i in g]
    by_id = {s.id: s for s in db.query(Story).filter(Story.id.in_(all_ids)).yield_per(2000)}

    groups = []
    for ids in id_groups:
        stories = [by_id[i] for i in ids if i in by_id]
        if len(stories) < 2 or len(stories) > MAX_PLAUSIBLE_COPIES:
            continue
        if len({s.site for s in stories}) < 2:
            continue
        # match_key stays the authority on identity: SQL narrows the candidates,
        # this confirms them, so the two definitions cannot silently drift apart.
        keys = {match_key(s.title, s.author) for s in stories}
        if len(keys) != 1 or None in keys:
            continue
        groups.append(stories)
    return groups


def merge_group(db: Session, stories: list[Story]) -> Story:
    """Merge a group of cross-posted stories into a single canonical row.

    - canonical = best copy (hosted / most-recently-updated / longest)
    - every other copy's URL is recorded in canonical.cross_post_urls
    - union the facet arrays so the canonical row is the richest
    - the non-canonical rows are deleted (their chapters cascade)
    Returns the surviving canonical Story.
    """
    canonical = _best_updated(stories)
    others = [s for s in stories if s.id != canonical.id]

    # Collect all alternate URLs (existing cross-posts + the other rows' own URLs)
    alt_urls = set(canonical.cross_post_urls or [])
    for s in stories:
        for u in (s.cross_post_urls or []):
            alt_urls.add(u)
        if s.id != canonical.id and s.url:
            alt_urls.add(s.url)
    alt_urls.discard(canonical.url)

    # Union facet arrays for a richer canonical record
    def union(attr):
        vals = []
        seen = set()
        for s in [canonical, *others]:
            for v in (getattr(s, attr) or []):
                kl = v.lower()
                if kl not in seen:
                    seen.add(kl); vals.append(v)
        return vals

    canonical.fandoms = union("fandoms")
    canonical.characters = union("characters")
    canonical.relationships = union("relationships")
    canonical.tags = union("tags")
    canonical.warnings = union("warnings")
    canonical.categories = union("categories")
    canonical.is_crossover = len(canonical.fandoms) > 1
    canonical.cross_post_urls = sorted(alt_urls)

    # Keep the best metadata numbers across copies
    canonical.kudos = max((s.kudos or 0) for s in stories)
    canonical.hits = max((s.hits or 0) for s in stories)
    canonical.bookmarks = max((s.bookmarks or 0) for s in stories)
    canonical.word_count = max((s.word_count or 0) for s in stories)

    # Delete the now-merged duplicates (chapters cascade via relationship)
    for s in others:
        db.delete(s)

    return canonical


def find_crosspost_for(db: Session, title: str, author: str, exclude_url: str | None = None) -> Story | None:
    """Given an incoming story's title/author, find an existing canonical row it
    should attach to (different site). Used at persist time to avoid creating a
    duplicate work row. Returns the existing Story or None."""
    k = match_key(title, author)
    if not k:
        return None
    nt, na = k.split("::", 1)
    # Cheap candidate fetch by exact author match, then verify normalized title.
    candidates = (
        db.query(Story)
        .filter(Story.author.ilike(author.strip()))
        .limit(50)
        .all()
    )
    for c in candidates:
        if exclude_url and c.url == exclude_url:
            continue
        if match_key(c.title, c.author) == k:
            return c
    return None
