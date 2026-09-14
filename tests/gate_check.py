"""Do any of these works carry gated terms? Checked against the LISTS.

Not against gate_underage/gate_adult: the columns are what a backfill
populates, so during one they are exactly the thing that cannot be trusted, and
"no row is flagged" would pass on a table where nothing had been flagged yet.
"""
import os
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text  # noqa: E402

import content_gates as cg  # noqa: E402
from db.session import db_session  # noqa: E402

SQL = """
SELECT count(*) FILTER (WHERE COALESCE(warnings,'{}') && CAST(:uw AS text[])
                           OR COALESCE(tags,'{}')     && CAST(:ut AS text[])),
       count(*) FILTER (WHERE COALESCE(warnings,'{}') && CAST(:aw AS text[])
                           OR COALESCE(tags,'{}')     && CAST(:at AS text[])),
       count(*)
  FROM stories WHERE id = ANY(CAST(:ids AS uuid[]))
"""

ids = [l.strip() for l in open(sys.argv[1]) if l.strip()]
with db_session() as db:
    row = db.execute(text(SQL), {
        "uw": cg.UNDERAGE_WARNINGS, "ut": cg.UNDERAGE_TAGS,
        "aw": cg.ADULT_WARNINGS, "at": cg.ADULT_TAGS, "ids": ids}).first()
print(f"{row[0]} {row[1]} {row[2]}")
