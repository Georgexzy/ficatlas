"""Author pages: /author/<slug>, one per well-read author.

    docker exec ficatlas-worker-1 python author_hubs.py [--limit N]

Why
---
Search Console (2026-10-06, three months): about 36 of the queries this site
appeared for were "<username> ao3" -- people looking for a particular writer.
The search box can already answer that (`author:` or plain text), but search
URLs are blocked in robots.txt, so Google can never index an answer to it. A
page per author is the only way that searcher lands here, and it shows one
thing no archive can: what that name has posted on AO3, FanFiction.net and
FictionAlley together.

Which authors
-------------
Not all of them. author_facets alone holds 183,806 names with 10+ works, and
Googlebot fetches a few dozen URLs a day here -- a sitemap of 200k author pages
would bury the hubs that already earn traffic. So the authors whose writing is
READ: at least MIN_WORKS indexed works with one in the top 10% of its archive
(`popularity`, a per-site percentile), ranked by how many of their works are,
capped at LIMIT.

Who never gets a page
---------------------
- An author who told us not to index them: `author_permissions.policy = 'deny'`
  (their works are also delisted, but the name must not become a URL either).
- Delisted works never count and are never listed.
- Placeholder names: orphan_account, Anonymous and the like.
- A name that is really a FANDOM: 115,457 FF.net rows carry "Axis Powers" as
  their author, an import artifact of Hetalia: Axis Powers. Any name that is
  also a fandom, character or pairing name is skipped.
- A name carrying a sexual or abusive word, by the same broad pattern the trope
  pages use: the slug is a public URL.

The same name is not always the same person
-------------------------------------------
"alex" on AO3 and "alex" on FanFiction.net may be two people. The page groups
works by the NAME as each archive shows it and says so; it never claims they
are one person.

Works are content-gated at build AND at read (api/hubs.py), like every page a
search engine hands to a stranger.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from fandom_hubs import slugify
from db.session import lift_statement_timeout
from hub_build import RANK, content_at_sql
from topic_hubs import _names

log = logging.getLogger(__name__)

LIMIT = 10000           # pages built (and listed in the sitemap)
MIN_WORKS = 3
POP_FLOOR = 0.9         # one work in the top 10% of its archive
WORKS_PER_SITE = 50
MAX_WORKS = 10000       # the most prolific real author here has ~6,000

_PLACEHOLDER = re.compile(
    r"^(orphan_account|anonymous|anon|unknown|guest|deleted|none|n/?a|admin|"
    r"various|multiple authors?|\[deleted\]|unknown author)$", re.I)

# Usernames run words together ("crimsonfucker05"), so the trope pages'
# whole-word pattern misses them. A substring check, narrowed so ordinary names
# survive: "Cumberbatch", "Essex", "grapes", "torpedo" and "Dickens" all pass.
_UNSAFE_NAME = re.compile(
    r"fuck|porn|smut|nsfw|slut|whore|pussy|penis|horny|incest|shota|"
    r"(?<![gdcst])rape(?!seed)|lolicon|\bloli\b|(?<!tor)pedo(?!met)|pedophil|paedo|"
    r"\bsex(?:y|ual)?\b|\bcock\b|\bkink|underage|bdsm|\bpwp\b|dubcon|noncon",
    re.I)

DDL = """
CREATE TABLE IF NOT EXISTS author_hubs (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    author_key  TEXT NOT NULL,
    work_count  INTEGER NOT NULL DEFAULT 0,
    top_ids     TEXT[] NOT NULL DEFAULT '{}',
    top_by_site JSONB NOT NULL DEFAULT '{}'::jsonb,
    site_counts JSONB,
    fandoms     TEXT[] NOT NULL DEFAULT '{}',
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_author_hubs_key ON author_hubs (author_key)
"""


def usable_name(name: str, names: set[str]) -> bool:
    n = (name or "").strip()
    if len(n) < 2 or len(n) > 80:
        return False
    # Not topic_hubs._UNSAFE: that is broad on purpose for TROPE names and,
    # matched inside a username, refuses "EssexGirl", "grapes_of_wrath" and
    # "Sussex". Usernames get their own narrower pattern above.
    if _PLACEHOLDER.match(n) or _UNSAFE_NAME.search(n):
        return False
    if n.lower() in names:
        return False
    return bool(slugify(n))


def candidates(db: Session, limit: int) -> list[tuple[str, str, int]]:
    """(author_key, display name, works) for the most-read authors.

    One grouped scan of the table (~1 minute). The display name is the
    most-used casing of the key.
    """
    rows = db.execute(text("""
        WITH a AS (
            SELECT lower(author) AS k, author, count(*) AS n,
                   count(*) FILTER (WHERE popularity >= :floor) AS good,
                   max(popularity) AS best
              FROM stories
             WHERE author IS NOT NULL AND delisted_at IS NULL
             GROUP BY lower(author), author
        )
        SELECT k, (array_agg(author ORDER BY n DESC))[1] AS display,
               sum(n)::int AS works, sum(good) AS good, max(best) AS best
          FROM a
         GROUP BY k
        HAVING sum(n) >= :min AND max(best) >= :floor
         ORDER BY sum(good) DESC, max(best) DESC
         LIMIT :lim
    """), {"floor": POP_FLOOR, "min": MIN_WORKS, "lim": limit * 2}).fetchall()
    denied = {r[0] for r in db.execute(text("""
        SELECT lower(author) FROM author_permissions
         WHERE policy = 'deny' AND revoked_at IS NULL
    """)).fetchall()}
    return [(k, d, int(w)) for k, d, w, _, _ in rows if k not in denied]


def build(db: Session, limit: int = LIMIT, trial: int | None = None) -> int:
    db.execute(text("SET statement_timeout = 0"))
    for stmt in DDL.split(";"):
        if stmt.strip():
            db.execute(text(stmt))
    db.commit()
    started = db.execute(text("SELECT now()")).scalar_one()
    names = _names(db)
    taken: set[str] = set()
    written = 0
    for key, display, _ in candidates(db, trial or limit):
        if written >= (trial or limit):
            break
        if not usable_name(display, names):
            continue
        slug = slugify(display)
        # Two names can fold to one slug ("Alex_M" and "alex-m"); the second
        # gets a suffix rather than overwriting the first.
        base, i = slug, 2
        while slug in taken:
            slug, i = f"{base}-{i}", i + 1
        taken.add(slug)
        try:
            ranked = db.execute(text(f"""
                SELECT site, id FROM (
                    SELECT site, id, row_number() OVER (PARTITION BY site
                                                        ORDER BY {RANK}) rn
                      FROM stories
                     WHERE lower(author) = :k
                       AND delisted_at IS NULL AND source_restricted_at IS NULL
                       AND NOT gate_adult AND NOT gate_underage
                ) r WHERE rn <= :n ORDER BY site, rn
            """), {"k": key, "n": WORKS_PER_SITE}).fetchall()
            counts = dict(db.execute(text("""
                SELECT site::text, count(*) FROM stories
                 WHERE lower(author) = :k AND delisted_at IS NULL
                   AND NOT gate_adult AND NOT gate_underage
                 GROUP BY 1
            """), {"k": key}).fetchall())
            fandoms = [r[0] for r in db.execute(text("""
                SELECT f, count(*) FROM stories, unnest(fandoms) f
                 WHERE lower(author) = :k AND delisted_at IS NULL
                 GROUP BY f ORDER BY 2 DESC LIMIT 6
            """), {"k": key}).fetchall()]
        except Exception:
            log.exception("author %s failed", key)
            db.rollback()
            continue
        if not ranked:
            continue        # everything they wrote is gated: no public page
        # An import artifact, not a person: the FF.net bulk dump put the
        # fandom's own name in the author field for 115,457 Hetalia rows
        # ("Axis Powers" inside "Hetalia - Axis Powers"). A name found inside
        # the fandom it mostly writes in, or a body of work no person writes,
        # is not an author page.
        total = sum(int(v) for v in counts.values())
        if total > MAX_WORKS or any(display.lower() in f.lower() for f in fandoms[:2]):
            db.execute(text("DELETE FROM author_hubs WHERE slug = :s"), {"s": slug})
            continue
        by_site: dict[str, list[str]] = {}
        for site, sid in ranked:
            by_site.setdefault(str(getattr(site, "value", site)), []).append(str(sid))
        top = [i for pos in range(WORKS_PER_SITE) for s in sorted(by_site)
               for i in by_site[s][pos:pos + 1]]
        db.execute(text(f"""
            INSERT INTO author_hubs (slug, name, author_key, work_count, top_ids,
                                     top_by_site, site_counts, fandoms,
                                     built_at, content_at)
            VALUES (:slug, :name, :k, :wc, :top, CAST(:bs AS jsonb),
                    CAST(:sc AS jsonb), :fandoms, now(), now())
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name, author_key = EXCLUDED.author_key,
                work_count = EXCLUDED.work_count, top_ids = EXCLUDED.top_ids,
                top_by_site = EXCLUDED.top_by_site,
                site_counts = EXCLUDED.site_counts, fandoms = EXCLUDED.fandoms,
                built_at = EXCLUDED.built_at,
                content_at = {content_at_sql('author_hubs')}
        """), {"slug": slug, "name": display, "k": key,
               "wc": sum(int(v) for v in counts.values()), "top": top,
               "bs": json.dumps(by_site),
               "sc": json.dumps({str(k): int(v) for k, v in counts.items()}),
               "fandoms": fandoms})
        written += 1
        if written % 200 == 0:
            db.commit()
            # The pool recycles connections at 30 minutes and a fresh one
            # carries the default timeout (CLAUDE.md, maintenance passes).
            lift_statement_timeout(db)
            log.info("authors: %d pages", written)
    db.commit()
    if not trial:
        gone = db.execute(text("DELETE FROM author_hubs WHERE built_at < :t"),
                          {"t": started}).rowcount
        db.commit()
        if gone:
            log.info("authors: removed %d stale pages", gone)
    return written


if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, "/app")
    from db.session import SessionLocal
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="trial: build only this many, prune nothing")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        print(build(db, trial=a.limit))
    finally:
        db.close()
