"""Trope hubs: one page per (fandom, trope), e.g. /fandom/harry-potter/time-travel.

Why
---
Measured over 30 days of traffic, by where a visitor first landed:

    landed on (from a search engine)   opened a story   went to an archive
    pairing hub                              47%               28%
    fandom hub                               46%               17%
    home / search page                       16%               10%

Hub pages turn a stranger into a reader three times as well as the front door,
and they are the pages search engines send people to. What fans actually type
into a search engine is a fandom and a trope — "harry potter time travel
fanfiction", "naruto slow burn fic" — and no page here answered that: the only
route to it was a search URL robots.txt keeps crawlers out of. These pages are
bounded and real: the most-read works for that fandom and trope, per archive.

What is chosen, and what never is
---------------------------------
Candidate tropes come from the tags on each fandom's most-read 15,000 works,
so they are what readers of THAT fandom actually look for. Then a long list of
things that must not become a public page is removed: import markers, archive
metadata ("spoilers: PoA", "era: R"), character and pairing names (those have
their own hubs), AO3's placeholder tags, and anything sexual, abusive or on the
content-gate lists — these pages have no content toggle and their URLs are
shared, so the tag NAME has to be safe as well as the works under it. Works are
gated at build time by column and again at read time by column and array, as
every hub is (api/hubs.py).

Offline, weekly, behind the shared heavy-pass lock. ~1s per page.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from fandom_hubs import slugify
from gate_terms import ADULT_TAGS, UNDERAGE_TAGS
from hub_build import RANK, content_at_sql
from provenance import PROVENANCE_TAGS

log = logging.getLogger(__name__)

FANDOMS = 60            # the largest fandom hubs get trope pages
PER_FANDOM = 24         # at most this many tropes each
MIN_WORKS = 300         # a trope page needs a real body of work behind it
SAMPLE = 15000          # most-read works per fandom used to pick tropes
WORKS_PER_SITE = 30

# Tags that say where a row came from or what an archive recorded about it,
# never what the story is.
_META = re.compile(
    r"^(?:spoilers?:|era:|dlp_|reddit_|community_recs|ao3_|ffnet_|hf_meta|"
    r"janelleshane|tropedia)|to be added|not beta|no beta|unbeta|tags? (?:will|to) be|"
    r"author'?s? note|work in progress|wip\b|i don'?t know how to tag|"
    r"tagging is hard|^\W*$", re.I)
# Never the name of a public page whose URL gets shared: sexual content, abuse,
# extreme violence, death. Broad on purpose — the cost of over-matching is one
# page not built; the cost of under-matching is a page nobody should land on.
_UNSAFE = re.compile(
    r"sex|smut|porn|pwp|kink|bdsm|explicit|nsfw|lemon|orgasm|masturbat|"
    r"rape|non-?con|dub-?con|consent|abuse|incest|underage|minor|pedo|paedo|"
    r"groom|torture|gore|violence|murder|suicid|self[- ]?harm|death|dead dove|"
    r"\bfirst time\b|blood|drug|alcohol|vore|mpreg|omegaverse|a/b/o|\bheat\b|\brut\b|\bknot|breed", re.I)

_GATED = {t.lower() for t in (*ADULT_TAGS, *UNDERAGE_TAGS)}


def usable_tag(tag: str, names: set[str]) -> bool:
    """Whether a tag may become a public trope page."""
    t = (tag or "").strip()
    if len(t) < 3 or len(t) > 60:
        return False
    if t in PROVENANCE_TAGS or t.lower() in _GATED:
        return False
    if _META.search(t) or _UNSAFE.search(t):
        return False
    if t.lower() in names:          # a character, pairing or fandom name
        return False
    # Pairings and friendships have their own hubs; a slash tag whose halves
    # are not names ("Hurt/Comfort") is a trope and stays.
    if " & " in t:
        return False
    if "/" in t and any(p.strip().lower() in names for p in t.split("/")):
        return False
    return True


def _names(db: Session) -> set[str]:
    rows = db.execute(text("""
        SELECT lower(value) FROM facets
         WHERE kind IN ('character', 'relationship', 'fandom', 'fandom_ao3')
           AND count >= 200
    """)).fetchall()
    return {r[0] for r in rows}


def candidates(db: Session, variants: list[str], names: set[str]) -> list[str]:
    rows = db.execute(text("""
        SELECT t, count(*) FROM (
            SELECT tags FROM stories
             WHERE fandoms && :v AND popularity IS NOT NULL
               AND NOT gate_adult AND NOT gate_underage
             ORDER BY popularity DESC LIMIT :n) s, unnest(tags) t
         GROUP BY t ORDER BY 2 DESC LIMIT 150
    """), {"v": variants, "n": SAMPLE}).fetchall()
    out = []
    for tag, _ in rows:
        if usable_tag(tag, names):
            out.append(tag)
        if len(out) >= PER_FANDOM * 2:
            break
    return out


def build(db: Session, fandoms: int = FANDOMS, per_fandom: int = PER_FANDOM,
          limit: int | None = None) -> int:
    db.execute(text("SET statement_timeout = 0"))
    started = db.execute(text("SELECT now()")).scalar_one()
    names = _names(db)
    hubs = db.execute(text("""
        SELECT slug, name, variants FROM fandom_hubs
         ORDER BY work_count DESC LIMIT :n
    """), {"n": limit or fandoms}).fetchall()
    written = 0
    for fslug, fname, variants in hubs:
        made = 0
        for tag in candidates(db, list(variants), names):
            if made >= per_fandom:
                break
            try:
                ranked = db.execute(text(f"""
                    SELECT site, id FROM (
                        SELECT site, id, row_number() OVER (PARTITION BY site
                                                            ORDER BY {RANK}) rn
                          FROM stories
                         WHERE fandoms && :v AND tags @> ARRAY[CAST(:t AS text)]
                           AND delisted_at IS NULL AND source_restricted_at IS NULL
                           AND NOT gate_adult AND NOT gate_underage
                    ) r WHERE rn <= :n ORDER BY site, rn
                """), {"v": list(variants), "t": tag, "n": WORKS_PER_SITE}).fetchall()
                counts = dict(db.execute(text("""
                    SELECT site::text, count(*) FROM stories
                     WHERE fandoms && :v AND tags @> ARRAY[CAST(:t AS text)]
                       AND delisted_at IS NULL AND NOT gate_adult AND NOT gate_underage
                     GROUP BY 1
                """), {"v": list(variants), "t": tag}).fetchall())
            except Exception:
                log.exception("topic %s / %s failed", fslug, tag)
                db.rollback()
                continue
            total = sum(int(v) for v in counts.values())
            if total < MIN_WORKS or not ranked:
                continue
            by_site: dict[str, list[str]] = {}
            for site, sid in ranked:
                by_site.setdefault(str(getattr(site, "value", site)), []).append(str(sid))
            top = [i for pos in range(WORKS_PER_SITE) for s in sorted(by_site)
                   for i in by_site[s][pos:pos + 1]]
            tslug = slugify(tag)
            if not tslug:
                continue
            db.execute(text(f"""
                INSERT INTO topic_hubs (slug, fandom_slug, fandom_name, tag, name,
                                        work_count, top_ids, top_by_site,
                                        site_counts, built_at, content_at)
                VALUES (:slug, :fs, :fn, :tag, :name, :wc, :top,
                        CAST(:bs AS jsonb), CAST(:sc AS jsonb), now(), now())
                ON CONFLICT (slug) DO UPDATE SET
                    fandom_name = EXCLUDED.fandom_name, tag = EXCLUDED.tag,
                    name = EXCLUDED.name, work_count = EXCLUDED.work_count,
                    top_ids = EXCLUDED.top_ids, top_by_site = EXCLUDED.top_by_site,
                    site_counts = EXCLUDED.site_counts, built_at = EXCLUDED.built_at,
                    content_at = {content_at_sql('topic_hubs')}
            """), {"slug": f"{fslug}/{tslug}", "fs": fslug, "fn": fname, "tag": tag,
                   "name": f"{fname} · {tag}", "wc": total, "top": top,
                   "bs": json.dumps(by_site),
                   "sc": json.dumps({str(k): int(v) for k, v in counts.items()})})
            db.commit()
            made += 1
            written += 1
        log.info("topics: %s — %d pages", fname, made)
    if not limit:
        gone = db.execute(text("DELETE FROM topic_hubs WHERE built_at < :t"),
                          {"t": started}).rowcount
        db.commit()
        if gone:
            log.info("topics: removed %d stale pages", gone)
    return written


if __name__ == "__main__":
    import argparse
    from db.session import SessionLocal
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Rebuild trope hub pages.")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the N largest fandoms, and prune nothing (trial)")
    a = ap.parse_args()
    with SessionLocal() as s:
        log.info("wrote %d topic pages", build(s, limit=a.limit))
