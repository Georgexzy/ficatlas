"""Remove indexed works whose summary carries an explicit no-external-sites
notice from the author.

A one-time sweep: it walks the whole stories table once (via a broad SQL
pre-filter so the precise regex only runs on the few candidate rows), and
deletes any story whose summary `external_optout.has_external_optout` flags.

Deleting a Story row cascades to its hosted chapters (FK ondelete=CASCADE), so
full text goes with the index entry — the operator chose to remove both.

Dry-run by default: pass --apply to actually delete.
"""

import argparse
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text as sql_text

from db.session import db_session
from models.story import Story
from external_optout import match_external_optout

# Broad pre-filter so the regex only ever runs on a handful of rows instead of
# a 19.8M-row scan. Covers every trigger word the matcher can fire on.
_BROAD = sql_text("""
    SELECT id, site, site_id, title, summary FROM stories
    WHERE summary IS NOT NULL AND summary <> ''
      AND (
          summary ILIKE '%repost%' OR summary ILIKE '%re-post%'
       OR summary ILIKE '%republish%' OR summary ILIKE '%re-publish%'
       OR summary ILIKE '%redistribut%' OR summary ILIKE '%re-distribut%'
       OR summary ILIKE '%re-upload%'
       OR summary ILIKE '%no permission%'
       OR (
              (summary ILIKE '%other site%' OR summary ILIKE '%other website%'
            OR summary ILIKE '%another site%' OR summary ILIKE '%elsewhere%'
            OR summary ILIKE '%goodreads%'   OR summary ILIKE '%storygraph%'
            OR summary ILIKE '%other platform%')
          AND (summary ILIKE '%do not%' OR summary ILIKE '%don''t%'
            OR summary ILIKE '%dont%'  OR summary ILIKE '%may not%')
       )
      )
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; default is a dry run")
    args = ap.parse_args()

    with db_session() as db:
        db.execute(sql_text("SET statement_timeout = 0"))
        rows = db.execute(_BROAD).fetchall()

    matched = [r for r in rows if match_external_optout(r.summary)]
    matched.sort(key=lambda r: (r.site, r.site_id))

    print(f"candidates={len(rows)} matched={len(matched)} "
          f"({'APPLY' if args.apply else 'dry run'})")
    for r in matched:
        snippet = match_external_optout(r.summary)
        print(f"  [{r.site}] {r.site_id}  {r.title[:50]!r}")
        print(f"      match: {snippet}")

    if args.apply and matched:
        with db_session() as db:
            ids = [r.id for r in matched]
            deleted = db.query(Story).filter(Story.id.in_(ids)).delete(
                synchronize_session=False)
            db.commit()
        print(f"deleted {deleted} story rows (hosted chapters cascade)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
