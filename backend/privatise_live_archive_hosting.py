r"""
Move globally-hosted text off the public shelf unless its archive is gone.
=========================================================================

FicAtlas indexes ~19.7M works as metadata and serves the full TEXT of about
30,000. Indexing metadata is what a search engine does. Serving somebody's
complete story to the public is republishing it, and the case for doing that
rests entirely on the original being unreachable:

    FictionAlley closed. Its 29,949 works are not on fictionalley.org any more,
    and for most there is no other copy. Taking them down here does not send a
    reader to the author's own page — there is no author's page left. This is
    preservation, which is the one argument that actually holds.

    AO3 and FanFiction.net are alive. Their authors are posting there right now,
    can edit or delete at will, and are one click away. Rehosting those adds
    nothing a link does not, and it is the exact thing r/FanFiction objects to:
    work "being stolen en masse". No preservation argument covers it, because
    nothing is at risk of being lost.

So the rule this script enforces:

    text is PUBLIC only if its source archive is dead.
    everything else moves to the owner's private shelf.

Private means a row in `user_hosted` and `is_hosted = false`. The story stays in
the shared tables — dedup and cross-post matching still need it, and its
metadata entry stays searchable exactly as before — but `may_read_text` will
only hand the text to the one account that holds it. Not to other readers, not
to other admins.

Nothing is deleted. This is a change of who may read, and it reverses by
flipping is_hosted back.

    docker compose exec backend python privatise_live_archive_hosting.py --dry-run
    docker compose exec backend python privatise_live_archive_hosting.py --owner george
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://ficatlas:ficatlas@db:5432/ficatlas")

from sqlalchemy import text as sql_text

from db.session import db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Archives that no longer serve their own work, so a copy here is the only copy.
#
# Matched on the URL host rather than the `site` column: `site` says which
# archive a row was catalogued under, and EPUB uploads carry site='ao3' while
# having no AO3 page at all. What matters is whether the address in `url` still
# resolves to the author's own copy, and that is a property of the host.
DEAD_ARCHIVE_HOSTS = {
    "fictionalley.org",
    "www.fictionalley.org",
    # HPFanficArchive — closed 2021, archived only in Wayback captures.
    "hpfanficarchive.com",
    "www.hpfanficarchive.com",
    "astronomytower.org",
    "www.astronomytower.org",
    "schnoogle.com",
    "www.schnoogle.com",
}

_HOST_SQL = r"regexp_replace(url, '^https?://([^/]+).*', '\1')"

# Rows whose text is public but whose source is not a dead archive. Deliberately
# expressed as "not in the dead list" rather than "in a live list": a host nobody
# has classified yet is treated as live, so the failure mode of forgetting to
# update this file is a story staying private rather than one being republished.
_CANDIDATES = sql_text(f"""
    SELECT id, title, author, site, url
    FROM stories
    WHERE is_hosted
      AND COALESCE({_HOST_SQL}, '') NOT IN :dead
    ORDER BY site, title
""")


def run(owner: str, dry_run: bool) -> int:
    with db_session() as db:
        row = db.execute(sql_text(
            "SELECT id, username, role FROM users WHERE lower(username) = lower(:u)"
        ), {"u": owner}).first()
        if not row:
            log.error(f"no such user: {owner!r} — pass --owner with an existing account")
            return 2
        owner_id, owner_name, owner_role = str(row[0]), row[1], row[2]

        rows = db.execute(
            _CANDIDATES.bindparams(dead=tuple(DEAD_ARCHIVE_HOSTS))).fetchall()

        if not rows:
            log.info("nothing to move — every hosted work is from a dead archive")
            return 0

        by_site: dict[str, int] = {}
        for r in rows:
            site = r[3].value if hasattr(r[3], "value") else str(r[3])
            by_site[site] = by_site.get(site, 0) + 1

        log.info(f"{len(rows):,} publicly-hosted works are NOT from a dead archive:")
        for site, n in sorted(by_site.items(), key=lambda kv: -kv[1]):
            log.info(f"    {site:<14} {n:,}")
        log.info(f"  they would move to {owner_name}'s private shelf (role={owner_role})")
        for r in rows[:10]:
            log.info(f"    · {(r[1] or '?')[:60]:<60} {(r[4] or '')[:60]}")
        if len(rows) > 10:
            log.info(f"    … and {len(rows) - 10:,} more")

        # What STAYS public, reported too — the risk of a rule like this is not
        # that it moves too much but that it silently leaves something behind.
        kept = db.execute(sql_text(f"""
            SELECT {_HOST_SQL} AS host, count(*)
            FROM stories WHERE is_hosted
              AND COALESCE({_HOST_SQL}, '') IN :dead
            GROUP BY 1 ORDER BY 2 DESC
        """).bindparams(dead=tuple(DEAD_ARCHIVE_HOSTS))).fetchall()
        log.info("staying public (dead archives):")
        for h, n in kept:
            log.info(f"    {h:<28} {n:,}")

        if dry_run:
            log.info("DRY RUN — nothing changed")
            return 0

        ids = [str(r[0]) for r in rows]
        # Insert first, then unpublish. The other order would leave a window in
        # which the text is readable by nobody at all, and if the second half
        # failed it would be unreachable until someone noticed.
        db.execute(sql_text("""
            INSERT INTO user_hosted (user_id, story_id)
            SELECT :u, unnest(CAST(:ids AS uuid[]))
            ON CONFLICT DO NOTHING
        """), {"u": owner_id, "ids": ids})
        db.execute(sql_text("""
            UPDATE stories SET is_hosted = false
            WHERE id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": ids})
        db.commit()

        held = db.execute(sql_text(
            "SELECT count(*) FROM user_hosted WHERE user_id = :u"), {"u": owner_id}).scalar()
        still_public = db.execute(sql_text(
            "SELECT count(*) FROM stories WHERE is_hosted")).scalar()
        log.info(f"DONE — {len(ids):,} moved; {owner_name} now holds {held:,}; "
                 f"{still_public:,} works remain publicly readable")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--owner", default=os.getenv("PRIVATISE_OWNER", ""),
                    help="username whose private shelf receives the works")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.owner and not args.dry_run:
        ap.error("--owner is required (or use --dry-run to see what would move)")
    return run(args.owner or "", args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
