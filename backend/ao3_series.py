r"""
Read AO3's own series data instead of guessing at it.
=====================================================

series_detect.py infers series from title patterns, because no bulk dump carries
a series field. That was the right answer for FanFiction.net and FictionAlley,
which have no such concept at all — but it is the wrong answer for AO3, which
does, and publishes it in the HTML of every work page:

    <dd class="series">
      ← Previous Work
      Part 3 of <a href="/series/1027363">All the Young Dudes</a>
      Next Work →
    </dd>

That is the author's own grouping, their own name for it, and their own ordering.
No inference can beat it and none should be attempted where it exists. Calibre's
duplicate-finding plugins make the same distinction — fuzzy matching is what you
reach for when the metadata is absent, not a substitute for reading it.

Costs nothing extra. ao3_stub_enrich already fetches these pages for other
reasons, so this rides along on requests we were making anyway; a work with a
series simply now records one.

Stored with source='explicit', which the UI reports differently: an inferred
grouping is presented as our reading of the titles, an explicit one as the
author's own.
"""

import logging
import re

from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)

# The whole <dd class="series"> block, then the parts inside it. Split in two
# because the block also contains Previous/Next links whose anchors would
# otherwise be mistaken for the series link.
_BLOCK_RE = re.compile(r'<dd class="series">(.*?)</dd>', re.S)
# Tags between every token: AO3 writes the position as
#   Part <strong>2</strong> of <a href="/series/3418618">Name</a>
_PART_RE = re.compile(
    r'Part\s*(?:<[^>]+>\s*)*(\d+)\s*(?:</[^>]+>\s*)*of\s*'
    r'(?:<[^>]+>\s*)*?<a[^>]*href="/series/(\d+)"[^>]*>(.*?)</a>', re.S | re.I)
# A work can sit in several series, each its own <span class="position">.
_POSITION_RE = re.compile(r'<span class="position">(.*?)</span>', re.S)


def parse_series(html: str) -> list[dict]:
    """Every series this work page declares: name, AO3 id and position."""
    block = _BLOCK_RE.search(html or "")
    if not block:
        return []
    inner = block.group(1)
    chunks = _POSITION_RE.findall(inner) or [inner]
    out: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        m = _PART_RE.search(chunk)
        if not m:
            continue
        ao3_id = m.group(2)
        if ao3_id in seen:
            continue
        seen.add(ao3_id)
        name = re.sub(r"<[^>]+>", "", m.group(3))
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            continue
        out.append({"position": int(m.group(1)), "ao3_id": ao3_id, "name": name})
    return out


def record(db, story_id: str, author: str | None, entries: list[dict]) -> int:
    """Store what a work page declared. Returns how many series it joined.

    Keyed on the AO3 series id rather than the name, so two different series
    that happen to share a title stay separate — and so a series renamed by its
    author updates in place instead of forking.
    """
    n = 0
    for e in entries:
        key = f"ao3:{e['ao3_id']}"
        sid = db.execute(sql_text("""
            INSERT INTO series (name, author, site, source, confidence, work_count, ao3_id)
            VALUES (:n, :a, 'ao3', 'explicit', 1.0, 0, :k)
            ON CONFLICT (ao3_id) DO UPDATE
                SET name = EXCLUDED.name, source = 'explicit', confidence = 1.0
            RETURNING id
        """), {"n": e["name"], "a": author, "k": key}).scalar()
        db.execute(sql_text("""
            INSERT INTO series_works (series_id, story_id, position)
            VALUES (:s, :w, :p)
            ON CONFLICT (series_id, story_id) DO UPDATE SET position = EXCLUDED.position
        """), {"s": sid, "w": story_id, "p": e["position"]})
        db.execute(sql_text("""
            UPDATE series SET work_count =
                (SELECT count(*) FROM series_works WHERE series_id = :s)
            WHERE id = :s
        """), {"s": sid})
        n += 1
    return n
