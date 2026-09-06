"""What r/HPFanfiction actually recommends, as a signal the archives cannot give.

    docker exec ficatlas-backend-1 python reddit_recs_import.py --dry-run
    docker exec ficatlas-backend-1 python reddit_recs_import.py

The problem
-----------
"Most popular" sorts by `popularity`, which is a percentile blend of kudos,
bookmarks, comments and hits. That measures READERSHIP, and readership is not
the same thing as recommendation — the works a community presses on newcomers
year after year are often older, longer, plot-driven and on FanFiction.net,
which is precisely where this index has the least engagement data.

Measured against the r/HPFanfiction most-linked list (1,462 works, 2012-2023):

                recommended   in index   HAS a popularity score
    FF.net            1,131        655                      565
    AO3                 331        303                       61
    total             1,462        958                      626

So 836 of the most-recommended Harry Potter fanfics of the last decade could
not appear in "Most popular" at any position, because they have no engagement
figure at all and therefore no score. That is not a ranking bug — it is missing
data, and no amount of reweighting fixes it.

A reference count is the missing measurement. It counts how many times human
beings linked a work to another human being, which is a stronger claim about
quality than a kudos click and is completely independent of which archive the
work happens to live on.

Shape
-----
Two tags per matched work, mirroring `dlp_library` / `dlp_stars:4.67` exactly,
because that pattern already exists here for a curated quality list and the
search API already knows how to filter on it:

    reddit_recs           the marker — this work is on the list
    reddit_refs:1376      how many times it was linked

Matching is by ARCHIVE ID parsed out of the sheet's URL, never by title. Titles
collide constantly in fanfiction (this index holds five works called
"Manacled"), and a wrong match here would attach a decade of somebody else's
reputation to the wrong story.

Deliberately additive
---------------------
Only works already in the index are touched, and only these two tags are
written. Works on the list that are NOT indexed are reported and left alone —
they are a crawl target, not something to invent a row for.
"""

import argparse
import csv
import io
import logging
import os
import re
import sys
import urllib.request

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

from db.session import db_session  # noqa: E402

log = logging.getLogger("reddit_recs")

MARKER = "reddit_recs"
VALUE_PREFIX = "reddit_refs:"

# The published sheet, exported as CSV. The gid pins the "All Time" tab; the
# other tabs are per-year slices of the same works.
SHEET = ("https://docs.google.com/spreadsheets/d/"
         "1qbr5N5rynbNwbVRpv5plESaRvk6yQwhapInWmGhNAcs/export"
         "?format=csv&gid=1578034658")

_FFNET = re.compile(r"fanfiction\.net/s/(\d+)")
_AO3 = re.compile(r"archiveofourown\.org/works/(\d+)")

# Below this the "recommendation" is one or two people mentioning a link, which
# is noise rather than standing. The list is a decade long, so a work anybody
# actually presses on newcomers clears this easily.
MIN_REFS = int(os.getenv("REDDIT_RECS_MIN_REFS", "3"))


def parse(csv_text: str) -> list[tuple[str, str, int]]:
    """(site, site_id, refs) for every row that names an archive work.

    The sheet has two header rows — a merged banner over the year columns and
    then the real names — so the column positions are read from the SECOND row
    rather than assumed.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    if len(rows) < 3:
        return []
    header = rows[1]
    try:
        i_url, i_refs = header.index("URL"), header.index("REFS")
    except ValueError:
        log.error("sheet layout changed: no URL/REFS column in %r", header[:12])
        return []

    out: list[tuple[str, str, int]] = []
    for r in rows[2:]:
        if len(r) <= max(i_url, i_refs):
            continue
        url = (r[i_url] or "").strip()
        try:
            refs = int((r[i_refs] or "0").replace(",", "").strip() or 0)
        except ValueError:
            continue
        if refs < MIN_REFS:
            continue
        m, a = _FFNET.search(url), _AO3.search(url)
        if m:
            out.append(("ffnet", m.group(1), refs))
        elif a:
            out.append(("ao3", a.group(1), refs))
    return out


# One statement per work. `array_remove` drops any previous refs tag so a
# re-run corrects a count rather than accumulating one tag per import, and the
# marker is only appended when it is not already there.
_UPSERT = text(f"""
    UPDATE stories
       SET tags = (
             SELECT array_agg(t)
               FROM unnest(
                    array_append(
                      array_append(
                        ARRAY(SELECT x FROM unnest(coalesce(tags, '{{}}')) x
                               WHERE x <> :marker
                                 AND x NOT LIKE '{VALUE_PREFIX}%'),
                        :marker),
                      :value)) t)
     WHERE site::text = :site AND site_id = :site_id
       AND delisted_at IS NULL
""")


def run(dry_run: bool = False, source: str | None = None) -> dict:
    raw = (open(source, encoding="utf-8").read() if source
           else urllib.request.urlopen(SHEET, timeout=60).read().decode("utf-8"))
    entries = parse(raw)
    stats = {"listed": len(entries), "matched": 0, "missing": 0, "updated": 0}
    if not entries:
        log.error("no usable rows parsed")
        return stats

    with db_session() as db:
        db.execute(text("SET statement_timeout = 0"))
        for site, site_id, refs in entries:
            found = db.execute(text(
                "SELECT 1 FROM stories WHERE site::text = :s AND site_id = :i"
                " AND delisted_at IS NULL LIMIT 1"),
                {"s": site, "i": site_id}).first()
            if not found:
                stats["missing"] += 1
                continue
            stats["matched"] += 1
            if dry_run:
                continue
            res = db.execute(_UPSERT, {"site": site, "site_id": site_id,
                                       "marker": MARKER,
                                       "value": f"{VALUE_PREFIX}{refs}"})
            stats["updated"] += res.rowcount or 0
        if not dry_run:
            db.commit()
    log.info("reddit recs: %s", stats)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Import r/HPFanfiction reference counts")
    ap.add_argument("--dry-run", action="store_true", help="match only, write nothing")
    ap.add_argument("--source", help="local CSV instead of fetching the sheet")
    a = ap.parse_args()
    run(dry_run=a.dry_run, source=a.source)
