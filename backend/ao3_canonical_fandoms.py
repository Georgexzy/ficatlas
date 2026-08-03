r"""
AO3 canonical fandom vocabulary
===============================

Populates facets(kind='fandom_ao3') with AO3's own canonical fandom names.

Why this exists
---------------
The Import tab's autocomplete used to fall back to AO3's /autocomplete/ endpoint
when what you typed did not match our index. That is precisely the case that
matters there — you are looking for a fandom to START scraping, so by definition
we do not have it yet — but AO3's robots.txt disallows /autocomplete/ and the box
called it on every keystroke, so it had to go.

AO3 publishes the same vocabulary as browsable pages that are NOT disallowed:

    /media                      -> 11 category links
    /media/<category>/fandoms   -> every canonical fandom in it, with work counts

One category page carries ~14,000 fandoms, so the whole vocabulary is ELEVEN
requests, refreshed occasionally, instead of one per keystroke. That is both
compliant and strictly better data: canonical names with AO3's own counts.

Names are stored exactly as AO3 renders them, which is what makes them safe to
paste into a discover job — picking one guarantees valid tag syntax and avoids
the malformed-tag URLs that broke earlier discover runs.

Usage
-----
    docker compose exec backend python ao3_canonical_fandoms.py
    docker compose exec backend python ao3_canonical_fandoms.py --dry-run
"""

import argparse
import html
import logging
import os
import re
import sys
import time

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

import httpx
from sqlalchemy import text as sql_text

from db.session import db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

BASE = "https://archiveofourown.org"
UA = {"User-Agent": "FicAtlas/1.0 (personal fanfiction index; +https://github.com/Georgexzy/ficatlas)"}

# The kind is deliberately separate from 'fandom'. That one is rebuilt from our
# own stories and its counts mean "works we hold"; these mean "works AO3 holds",
# and mixing them would put fandoms we have nothing for into search autocomplete,
# where every suggestion would lead to an empty result page.
KIND = "fandom_ao3"

CATEGORY_RE = re.compile(r'href="/media/([^"]+)/fandoms"')
FANDOM_RE = re.compile(r'<a class="tag" href="/tags/[^"]+/works">([^<]+)</a>\s*\((\d[\d,]*)\)')

# Courtesy delay. Eleven requests is nothing, but AO3 rate-limits harder than
# their robots.txt implies (see ao3_title_repair) so there is no reason to rush.
DELAY = 2.0


def fetch_categories(client: httpx.Client) -> list[str]:
    r = client.get(f"{BASE}/media", timeout=45, follow_redirects=True)
    r.raise_for_status()
    seen: list[str] = []
    for c in CATEGORY_RE.findall(r.text):
        if c not in seen:
            seen.append(c)
    return seen


def fetch_fandoms(client: httpx.Client, category: str) -> list[tuple[str, int]]:
    r = client.get(f"{BASE}/media/{category}/fandoms", timeout=90, follow_redirects=True)
    if r.status_code != 200:
        log.warning(f"  {category}: HTTP {r.status_code}")
        return []
    out: list[tuple[str, int]] = []
    for name, count in FANDOM_RE.findall(r.text):
        name = html.unescape(name).strip()
        if name:
            out.append((name[:500], int(count.replace(",", ""))))
    return out


def run(dry_run: bool) -> None:
    collected: dict[str, int] = {}
    with httpx.Client(headers=UA) as client:
        categories = fetch_categories(client)
        log.info(f"{len(categories)} media categories")
        for cat in categories:
            time.sleep(DELAY)
            rows = fetch_fandoms(client, cat)
            log.info(f"  {cat}: {len(rows):,} fandoms")
            for name, count in rows:
                # A fandom can appear under more than one category; keep the
                # larger count rather than whichever page happened to come last.
                if count > collected.get(name, -1):
                    collected[name] = count

    log.info(f"{len(collected):,} distinct canonical fandoms")
    if dry_run:
        for name in list(collected)[:15]:
            log.info(f"    {name}  ({collected[name]:,})")
        return

    # Replace wholesale inside one transaction: readers either see the previous
    # vocabulary or the new one, never a half-populated table. Cheap because
    # this kind is small and nothing else writes it.
    with db_session() as db:
        db.execute(sql_text("DELETE FROM facets WHERE kind = :k"), {"k": KIND})
        db.execute(
            sql_text("INSERT INTO facets (kind, value, count) VALUES (:k, :v, :c) "
                     "ON CONFLICT (kind, value) DO UPDATE SET count = EXCLUDED.count"),
            [{"k": KIND, "v": v, "c": c} for v, c in collected.items()],
        )
        db.commit()
    log.info(f"stored {len(collected):,} rows as kind={KIND}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync AO3 canonical fandom names into facets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.dry_run)


if __name__ == "__main__":
    main()
