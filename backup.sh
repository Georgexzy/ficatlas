#!/usr/bin/env bash
# FicAtlas backup.
#
# Not everything in this database is equally replaceable, and that drives the
# design. The bulk metadata — ~19M AO3/FF.net rows — can always be re-downloaded
# and re-imported from the public dumps. But the hosted full text cannot:
#
#   fictionalley   29,749 stories   1.86 GB of chapter text
#
# FictionAlley is a dead site. That text exists only because someone preserved a
# pg_dump of it. If this volume is lost, it is gone for good. The same goes for
# anything you imported by URL or uploaded as EPUB, and for user accounts.
#
# So there are two modes:
#
#   essential (default)  accounts and sessions; every story whose text we hold —
#                        publicly hosted AND privately imported — with its
#                        chapters; the user_hosted rows that make a private
#                        import reachable by its owner; follows; author
#                        permissions and takedown records; site settings.
#                        A few GB. This is the irreplaceable part.
#   full                 the entire database including all bulk metadata. Large
#                        and slow, but a single-file restore.
#
# Usage:
#   ./backup.sh                       # essential -> ./backups/
#   ./backup.sh full                  # everything
#   ./backup.sh essential /mnt/nas    # somewhere off this machine
#
# Restore instructions are printed at the end and stored beside each dump.

set -euo pipefail

MODE="${1:-essential}"
DEST="${2:-$(cd "$(dirname "$0")" && pwd)/backups}"
KEEP="${KEEP:-7}"                     # how many dumps of each mode to retain
SERVICE=db
DB=ficatlas
USER=ficatlas

command -v docker >/dev/null || { echo "docker not found"; exit 1; }
cd "$(dirname "$0")"

# The DB password, not the username. This used to pass PGPASSWORD="$USER",
# which only ever worked because pg_hba.conf trusts connections over the local
# socket — and `docker compose exec` lands on that socket. The moment pg_hba is
# tightened, or the dump is taken over TCP, that silently becomes a login
# failure at 4am. Read the real value the way everything else does.
[ -f .env ] && set -a && . ./.env && set +a
PGPW="${POSTGRES_PASSWORD:-ficatlas}"

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/ficatlas-$MODE-$STAMP.dump"

compose() { docker compose "$@"; }

if ! compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "The '$SERVICE' service isn't running — start it with: docker compose up -d"
  exit 1
fi

echo "FicAtlas backup — mode=$MODE"
echo "  destination: $OUT"

case "$MODE" in
  full)
    # -Fc = custom format: compressed, and restorable selectively with pg_restore.
    compose exec -T -e PGPASSWORD="$PGPW" "$SERVICE" \
      pg_dump -U "$USER" -d "$DB" -Fc --no-owner --no-acl > "$OUT"
    ;;

  essential)
    # Accounts in full, plus ONLY the stories whose text we actually hold and the
    # chapters belonging to them. pg_dump's --table cannot express "rows matching
    # a predicate", so everything is staged into one schema and that schema is
    # dumped. Staging the account tables here too rather than passing -t for them:
    # combining -n with -t makes pg_dump's object selection restrictive in a way
    # that silently produced an 8KB dump containing essentially nothing.
    #
    # "Text we hold" is NOT `is_hosted` alone, and getting that wrong cost this
    # backup 28 stories / 678 chapters for two weeks without a word. A privately
    # imported work is `is_hosted = false` PLUS a row in user_hosted — see
    # privatise_live_archive_hosting.py, which introduced that split three days
    # after this script was written and silently narrowed what it covers. The
    # header above promises to protect "anything you imported by URL or uploaded
    # as EPUB"; that promise is this predicate. Keep the user_hosted arm.
    compose exec -T -e PGPASSWORD="$PGPW" "$SERVICE" bash -c "
      set -e
      psql -U $USER -d $DB -v ON_ERROR_STOP=1 -q >&2 <<'SQL'
        DROP SCHEMA IF EXISTS backup_subset CASCADE;
        CREATE SCHEMA backup_subset;
        CREATE TABLE backup_subset.stories AS
          SELECT * FROM public.stories s
          WHERE s.is_hosted
             OR s.id IN (SELECT story_id FROM public.user_hosted);
        -- Join the STAGED stories, not public.stories, so this can never drift
        -- from the predicate above.
        CREATE TABLE backup_subset.chapters AS
          SELECT c.* FROM public.chapters c
          JOIN backup_subset.stories s ON s.id = c.story_id;
        CREATE TABLE backup_subset.users         AS SELECT * FROM public.users;
        CREATE TABLE backup_subset.user_sessions AS SELECT * FROM public.user_sessions;
        CREATE TABLE backup_subset.user_data     AS SELECT * FROM public.user_data;
        -- user_hosted is not bookkeeping, it is the ACCESS CONTROL for a private
        -- import: see the case in api/stories.py for a viewer who privately
        -- imported the work. Restore the text without it and the story is in
        -- the database but reachable by nobody.
        -- Keep double quotes out of this block: it is all inside a bash -c
        -- argument, and one stray quote ends the argument and the backup.
        CREATE TABLE backup_subset.user_hosted   AS SELECT * FROM public.user_hosted;
        -- Reader-created and consent/legal state. None of it is derivable from
        -- a re-import of the public dumps, which is the test for belonging here.
        CREATE TABLE backup_subset.follows            AS SELECT * FROM public.follows;
        CREATE TABLE backup_subset.author_permissions AS SELECT * FROM public.author_permissions;
        CREATE TABLE backup_subset.takedowns          AS SELECT * FROM public.takedowns;
        CREATE TABLE backup_subset.app_settings       AS SELECT * FROM public.app_settings;
SQL
      pg_dump -U $USER -d $DB -Fc --no-owner --no-acl -n backup_subset
      psql -U $USER -d $DB -q -c 'DROP SCHEMA IF EXISTS backup_subset CASCADE;' >&2
    " > "$OUT"
    ;;

  *)
    echo "Unknown mode '$MODE' (expected: essential | full)"; exit 1 ;;
esac

SIZE=$(du -h "$OUT" | cut -f1)
echo "  wrote $SIZE"

# A dump you can't restore isn't a backup, so check the archive is readable and
# actually contains table data. pg_restore --list needs a SEEKABLE file for the
# custom format, so the dump is written to a temp file inside the container
# rather than piped to /dev/stdin (which silently fails on a pipe).
TOC=$(compose exec -T "$SERVICE" sh -c \
  'cat > /tmp/verify.dump && pg_restore --list /tmp/verify.dump; rm -f /tmp/verify.dump' \
  < "$OUT" 2>/dev/null || true)
TABLE_COUNT=$(printf '%s\n' "$TOC" | grep -c "TABLE DATA" || true)
if [ "${TABLE_COUNT:-0}" -lt 1 ]; then
  echo "  WARNING: dump contains no table data — treat it as suspect."
  exit 1
fi
echo "  verified: readable archive, $TABLE_COUNT table(s) of data"

cat > "$DEST/RESTORE.md" <<'DOC'
# Restoring a FicAtlas backup

    docker compose up -d db

## essential dump (accounts, hosted AND privately imported stories, chapters,
## private-library ownership, follows, consent records and site settings)

Everything lands in a staging schema `backup_subset`, so nothing in `public` is
touched until you choose to merge. Safe to run against a live database.

    cat ficatlas-essential-<stamp>.dump | docker compose exec -T db \
      pg_restore -U ficatlas -d ficatlas --no-owner

Inspect first if you like:

    docker compose exec db psql -U ficatlas -d ficatlas \
      -c 'SELECT count(*) FROM backup_subset.stories;'

Then merge back into the live tables. ON CONFLICT DO NOTHING means re-running is
harmless and existing rows win.

This merges column-by-column rather than with `INSERT ... SELECT *`. `SELECT *`
depends on the live table having exactly the columns, in exactly the order, that
it had on the day of the dump — and init_db.py adds columns as the app grows. So
the obvious form works perfectly right up until the one day you need it, then
fails with "INSERT has more target columns than expressions" (or, if a column was
dropped and types still line up, quietly writes data into the wrong column). This
form uses the columns the two schemas have IN COMMON, so an older dump restores
into a newer schema, leaving new columns at their defaults:

    docker compose exec -T db psql -U ficatlas -d ficatlas <<'SQL'
      DO $$
      DECLARE t text; cols text;
      BEGIN
        FOREACH t IN ARRAY ARRAY[
          'users','stories','chapters','user_sessions','user_data',
          'user_hosted','follows','author_permissions','takedowns','app_settings'
        ] LOOP
          SELECT string_agg(quote_ident(c.column_name), ', ')
            INTO cols
            FROM information_schema.columns c
           WHERE c.table_schema = 'backup_subset' AND c.table_name = t
             AND EXISTS (SELECT 1 FROM information_schema.columns p
                          WHERE p.table_schema = 'public'
                            AND p.table_name   = t
                            AND p.column_name  = c.column_name);
          CONTINUE WHEN cols IS NULL;   -- table not in this dump
          EXECUTE format(
            'INSERT INTO public.%I (%s) SELECT %s FROM backup_subset.%I ON CONFLICT DO NOTHING',
            t, cols, cols, t);
          RAISE NOTICE 'merged %', t;
        END LOOP;
      END $$;
      DROP SCHEMA backup_subset CASCADE;
    SQL

Order matters and is deliberate: `users` before `stories`/`user_hosted`, because
the foreign keys point that way.

## full dump

    cat ficatlas-full-<stamp>.dump | docker compose exec -T db \
      pg_restore -U ficatlas -d ficatlas --no-owner --clean --if-exists

## after any restore

    docker compose exec backend python init_db.py     # indexes / functions
    docker compose exec -T db psql -U ficatlas -d ficatlas -c 'ANALYZE stories;'
    curl -X POST 'http://localhost:8000/api/stats/refresh-facets?min_count=2'
DOC

# Retention. Two limits, because a count alone does not bound disk use: these
# dumps grow as more stories get hosted, so "keep 7" quietly turns into whatever
# 7 × today's size happens to be. The size cap is what actually protects the
# machine; the count is the everyday rule.
#
# Never prunes below MIN_KEEP, even under disk pressure. A backup policy that
# can delete its way to zero is worse than none — the moment you need it most
# (a failing disk) is exactly when the free-space rule would fire.
MIN_KEEP="${MIN_KEEP:-2}"
MAX_TOTAL_MB="${MAX_TOTAL_MB:-6000}"     # ceiling for all dumps of this mode
MIN_FREE_MB="${MIN_FREE_MB:-15000}"      # start pruning early if the disk is tight

prune_one() {
  local victim
  victim=$(ls -1t "$DEST"/ficatlas-"$MODE"-*.dump 2>/dev/null | tail -n 1)
  [ -n "$victim" ] || return 1
  echo "  pruning $(basename "$victim") ($(du -h "$victim" | cut -f1)) — $1"
  rm -f "$victim"
}
count_dumps() { ls -1 "$DEST"/ficatlas-"$MODE"-*.dump 2>/dev/null | wc -l; }
total_mb()    { du -cm "$DEST"/ficatlas-"$MODE"-*.dump 2>/dev/null | tail -1 | cut -f1; }
free_mb()     { df -Pm "$DEST" | tail -1 | awk '{print $4}'; }

# 1. The ordinary rule: keep the newest $KEEP.
while [ "$(count_dumps)" -gt "$KEEP" ]; do prune_one "over keep=$KEEP" || break; done

# 2. Size ceiling, so growing dumps cannot silently fill the disk.
while [ "$(count_dumps)" -gt "$MIN_KEEP" ] && [ "$(total_mb)" -gt "$MAX_TOTAL_MB" ]; do
  prune_one "over ${MAX_TOTAL_MB}MB total" || break
done

# 3. Disk pressure, regardless of how few dumps that leaves (above MIN_KEEP).
while [ "$(count_dumps)" -gt "$MIN_KEEP" ] && [ "$(free_mb)" -lt "$MIN_FREE_MB" ]; do
  prune_one "only $(free_mb)MB free, want ${MIN_FREE_MB}MB" || break
done

echo "  retained $(count_dumps) dump(s), $(total_mb)MB total, $(free_mb)MB free on disk"
if [ "$(free_mb)" -lt "$MIN_FREE_MB" ]; then
  echo "  WARNING: still below ${MIN_FREE_MB}MB free after pruning — the database" \
       "itself is $(docker compose exec -T db psql -U "$USER" -d "$DB" -tAc \
       "SELECT pg_size_pretty(pg_database_size('$DB'))" 2>/dev/null | tr -d ' ')." \
       "Consider backing up to another disk: ./backup.sh $MODE /mnt/somewhere"
fi

echo "Done. Restore instructions: $DEST/RESTORE.md"
