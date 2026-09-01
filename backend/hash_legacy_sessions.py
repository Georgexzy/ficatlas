"""Hash session tokens that predate `_token_hash`, in place.

`api/auth.py` stores a SHA-256 of the session token so that `user_sessions` is
not a list of working credentials. Rows written before that went in hold the
token verbatim, and nothing ever converted them -- so the hole the hashing
closed stayed open for those rows, in a table `backup.sh essential` dumps every
night and `backup-offsite.sh` copies to another machine.

Nobody is logged out by this. The browser holds the plaintext token; the row
becomes its hash; `_token_lookups` tries the hash first, so the next request
from that reader matches on the new value. Afterwards the stored value is
hash-shaped, and `_LOOKS_STORED` refuses a hash-shaped cookie -- so the same
value read out of a backup is no longer a way in.

Idempotent: a row that already looks like a stored hash is skipped.

    docker exec ficatlas-backend-1 python /app/hash_legacy_sessions.py --dry-run
    docker exec ficatlas-backend-1 python /app/hash_legacy_sessions.py
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, "/app")
from db.dsn import default_database_url  # noqa: E402
os.environ.setdefault("DATABASE_URL", default_database_url())

from sqlalchemy import text as sql_text  # noqa: E402

from api.auth import _LOOKS_STORED, _token_hash  # noqa: E402
from db.session import db_session  # noqa: E402

log = logging.getLogger("hash_legacy_sessions")


def needs_hashing(token: str) -> bool:
    """True for a token stored verbatim rather than as a sha256 hex digest."""
    return not _LOOKS_STORED.fullmatch(token or "")


def run(dry_run: bool = False) -> int:
    with db_session() as db:
        # `token` IS the primary key -- there is no id column -- so the update
        # rewrites the key itself. Safe: the new value is a sha256 of the old,
        # and a collision with an existing hashed row would mean two different
        # tokens hashing alike.
        rows = db.execute(sql_text("SELECT token FROM user_sessions")).fetchall()
        legacy = [t for (t,) in rows if needs_hashing(t)]
        log.info("%d sessions, %d stored in plaintext", len(rows), len(legacy))
        if dry_run or not legacy:
            return len(legacy)
        for tok in legacy:
            # Hashed with the SAME function auth uses, so the reader's existing
            # cookie resolves to exactly this value on their next request.
            db.execute(sql_text("UPDATE user_sessions SET token = :h WHERE token = :old"),
                       {"h": _token_hash(tok), "old": tok})
        db.commit()

        left = [t for (t,) in db.execute(sql_text(
            "SELECT token FROM user_sessions")).fetchall() if needs_hashing(t)]
        if left:
            log.error("%d rows still in plaintext after the update", len(left))
        else:
            log.info("every stored token is now a hash")
        return len(legacy)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    print(f"converted: {run(ap.parse_args().dry_run)}")
