"""The database DSN used when the environment does not supply one.

Every entry point reads `DATABASE_URL` from the environment and compose sets it
for the backend, the worker and every one-off script, so this is only reached
when something is run outside the stack. It used to be the literal
`postgresql://ficatlas:<the development password>@db:5432/ficatlas`, repeated in eighteen files.

That string was never the live credential — the real password lives in `.env`,
which has never been committed — but a credential-shaped literal in tracked
source is indistinguishable from a real leak to a scanner, which is how the
repository ends up with secret-scanning alerts that have to be triaged by hand
every time. It is also one copy-paste away from becoming a real leak the day
someone pastes a production password over it.

So the password is read from `POSTGRES_PASSWORD`, and when that is unset it is
omitted from the URL entirely. libpq then falls back to `~/.pgpass` or to a
trust/peer connection, and if neither applies the failure is an authentication
error naming the user — which is a better outcome than silently connecting to
whatever a stale default happens to still open.
"""

import os
from urllib.parse import quote, urlsplit, urlunsplit


def default_database_url(host: str | None = None, dbname: str | None = None) -> str:
    """A DSN with no password literal in this file.

    `DATABASE_URL` first, with the database name swapped if one is asked for.
    That is where the credentials actually live in this deployment: compose
    sets DATABASE_URL on the backend and worker and sets POSTGRES_PASSWORD only
    on the `db` service, so a process inside the stack has no POSTGRES_* at all.
    Composing purely from those produced `postgresql://ficatlas@db/...` with no
    password and broke the FictionAlley importer, which is the one caller that
    asks for a DSN rather than just seeding the environment variable.

    `host` defaults to the compose service name; pass "localhost" for a process
    that runs on the host rather than inside the stack. It applies only to the
    POSTGRES_* path -- an explicit DATABASE_URL already names its own host.
    """
    existing = os.getenv("DATABASE_URL")
    if existing:
        if not dbname:
            return existing
        try:
            parts = urlsplit(existing)
            return urlunsplit(parts._replace(path="/" + quote(dbname, safe="")))
        except ValueError:
            pass        # unparseable: fall through and compose one

    user = os.getenv("POSTGRES_USER", "ficatlas")
    password = os.getenv("POSTGRES_PASSWORD", "")
    host = host or os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    name = dbname or os.getenv("POSTGRES_DB", "ficatlas")

    # Percent-encode both halves: a generated password is base64 and can carry
    # `+` `/` `=`, which change the meaning of the authority section raw.
    auth = quote(user, safe="")
    if password:
        auth += ":" + quote(password, safe="")

    return f"postgresql://{auth}@{host}:{port}/{name}"
