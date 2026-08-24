"""Tell search engines a hub page changed, without waiting to be crawled.

The problem this addresses is measured, not theoretical: `site:ficatlas.com`
returns nothing at all. 7,584 hub pages exist, they are in the sitemap, robots.txt
allows them and declares the sitemap — and none of it matters until a crawler
comes. For a new domain with no inbound links that can take months.

IndexNow inverts it: you POST the URLs that changed and the participating engines
(Bing, Yandex, Seznam, Naver — one submission is shared between them) fetch them.
It needs no account and no verification beyond hosting a key file, which is the
reason it is worth doing here and Google Search Console is not something this can
do for itself.

Two rules keep this honest rather than spammy, and both matter because abusing
IndexNow gets a host ignored:

  * only URLs whose content actually CHANGED are submitted. hub_build writes
    content_at only when the page would look different — not when the row is
    merely rebuilt — so it is exactly the right trigger, and the watermark below
    means a page is submitted once per change rather than once per run.
  * one request per run, capped. The protocol allows 10,000 URLs per POST.

Google does not participate. Nothing here substitutes for adding the property in
Search Console, which needs a human with the account.
"""
from __future__ import annotations

import logging
import os

import httpx
from sqlalchemy import text as sql_text

from db.session import db_session

log = logging.getLogger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"
SITE = os.getenv("PUBLIC_SITE_URL", "https://ficatlas.com").rstrip("/")
MAX_URLS = 10_000
WATERMARK_KEY = "indexnow_watermark"


def _changed_urls(db, since: str | None, limit: int) -> tuple[list[str], str | None]:
    """Hub URLs whose content changed after `since`, oldest first.

    Oldest first so the watermark can advance safely: if the cap truncates the
    list, the next run picks up exactly where this one stopped instead of
    skipping the middle.
    """
    urls: list[str] = []
    newest: str | None = None
    for table, path in (("fandom_hubs", "fandom"), ("ship_hubs", "ship")):
        rows = db.execute(sql_text(f"""
            SELECT slug, content_at FROM {table}
            WHERE content_at IS NOT NULL
              AND (:since IS NULL OR content_at > CAST(:since AS timestamptz))
            ORDER BY content_at ASC
            LIMIT :limit
        """), {"since": since, "limit": limit}).fetchall()
        for slug, changed in rows:
            urls.append(f"{SITE}/{path}/{slug}")
            stamp = changed.isoformat()
            if newest is None or stamp > newest:
                newest = stamp
    return urls[:limit], newest


def run(limit: int = MAX_URLS, dry_run: bool = False) -> int:
    """Submit changed hub URLs. Returns how many were sent."""
    key = os.getenv("INDEXNOW_KEY", "").strip()
    if not key:
        log.info("indexnow: no INDEXNOW_KEY set, skipping")
        return 0

    from api.settings import get_setting, put_setting

    with db_session() as db:
        since = (get_setting(db, WATERMARK_KEY) or "").strip() or None
        urls, newest = _changed_urls(db, since, min(limit, MAX_URLS))

        if not urls:
            log.info("indexnow: nothing changed since %s", since or "the beginning")
            return 0

        if dry_run:
            log.info("indexnow: would submit %d URLs (e.g. %s)", len(urls), urls[0])
            return len(urls)

        r = httpx.post(ENDPOINT, json={
            "host": SITE.split("//", 1)[-1],
            "key": key,
            # Explicit, because the key file lives at the site root while the
            # submitted URLs do not. Without it the engines look for the key
            # beside the first URL and reject the batch.
            "keyLocation": f"{SITE}/{key}.txt",
            "urlList": urls,
        }, timeout=30, headers={"Content-Type": "application/json; charset=utf-8"})

        # 200 accepted, 202 accepted-pending-key-validation. Anything else means
        # the batch was rejected and the watermark must NOT move, or those pages
        # would never be resubmitted.
        if r.status_code not in (200, 202):
            log.warning("indexnow: %s rejected the batch: %s", r.status_code,
                        r.text[:200])
            return 0

        if newest:
            put_setting(db, WATERMARK_KEY, newest)
        log.info("indexnow: submitted %d URLs (%s), watermark -> %s",
                 len(urls), r.status_code, newest)
        return len(urls)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Submit changed hub URLs to IndexNow")
    ap.add_argument("--limit", type=int, default=MAX_URLS)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(f"submitted: {run(limit=a.limit, dry_run=a.dry_run)}")
