#!/usr/bin/env bash
#
# ⚠ Run this as a FILE, not by pasting its contents (it calls `exit`, which in
#   an interactive shell closes your terminal):
#
#       /home/george/ficatlas/backup-offsite.sh
#
# Copy the newest local backup to the Windows share, whenever that machine
# happens to be on.
#
# The problem this solves: the backup target is a laptop, so it is off most of
# the time. A scheduled copy would fail most nights, and the failures would be
# indistinguishable from a broken backup. Instead of scheduling the copy, this
# watches for the laptop and copies opportunistically — the offsite copy is
# whatever the most recent moment of availability allowed.
#
# Deliberately light. It runs often and does nothing at all in the common case:
#
#   * one TCP probe to port 445, with a short timeout, if the host answers at
#     all. If the laptop is off this is the entire cost.
#   * nothing is copied unless a dump exists that is newer than what is already
#     on the share, so leaving the laptop on all day does not mean re-uploading
#     633MB every run.
#   * never generates a backup. backup.sh owns that; this only moves what
#     already exists, so a slow copy can never delay or corrupt one.
#
# The important guard: it refuses to write unless /mnt/windows is genuinely a
# mounted filesystem. Writing into an unmounted mountpoint silently fills the
# local disk with a "backup" that lives on the same disk it is meant to protect
# — the exact failure the offsite copy exists to prevent.
#
# Suggested cron (hourly is plenty; the copy only happens when there is
# something new AND the laptop is up):
#
#   17 * * * * /home/george/ficatlas/backup-offsite.sh >> /home/george/ficatlas/backups/offsite.log 2>&1

set -uo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a

HOST="${OFFSITE_HOST:-100.99.74.82}"
PORT="${OFFSITE_PORT:-445}"
MOUNT="${OFFSITE_MOUNT:-/mnt/windows}"
DEST_DIR="${OFFSITE_DIR:-$MOUNT/FicAtlasBackups}"
SRC_DIR="$(pwd)/backups"
KEEP="${OFFSITE_KEEP:-3}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }
quiet_exit() { exit 0; }   # laptop off is normal, not an error

# ── 1. Is the machine even there? ───────────────────────────────────────────
# Cheapest possible check, and the one that short-circuits almost every run.
timeout 4 bash -c "exec 3<>/dev/tcp/$HOST/$PORT" 2>/dev/null || quiet_exit

# ── 2. Is there anything worth copying? ─────────────────────────────────────
NEWEST=$(ls -1t "$SRC_DIR"/ficatlas-*.dump 2>/dev/null | head -1)
[ -n "$NEWEST" ] || { log "no local dump to copy"; exit 0; }

# ── 3. Mount if needed ──────────────────────────────────────────────────────
# setup-windows-share.sh adds `user` to the fstab entry so this works without
# root. If it has not been run yet, this fails and we leave quietly.
if ! mountpoint -q "$MOUNT" 2>/dev/null; then
  # A mount failure HERE is a fault, not an absent laptop, and it must not be
  # reported like one. The reachability probe above has already confirmed the
  # machine is up and answering on 445 — so if the mount still fails, something
  # is wrong with the configuration and nobody will ever be told unless this
  # says so. It used to swallow the error and `exit 0`, which is the same thing
  # a switched-off laptop does, and that is precisely how a credentials file
  # readable only by root went unnoticed: the mount failed with EACCES on every
  # run for weeks while the log said the calm thing.
  mount_err=$(mount "$MOUNT" 2>&1) || {
    log "ERROR: $HOST is up but $MOUNT would not mount: ${mount_err:-unknown error}"
    # The exact command, not a pointer to setup-windows-share.sh. That script
    # re-derives the credentials from /etc/fstab, and they were deliberately
    # moved OUT of fstab when it was first run — so re-running it now just exits
    # with "Could not find username=/password=". The existing file already holds
    # the right credentials; only its ownership is wrong.
    log "       fix: sudo chown root:$(id -un) /etc/samba/ficatlas.cred && sudo chmod 640 /etc/samba/ficatlas.cred"
    exit 1
  }
  WE_MOUNTED=1
fi

# Belt and braces: mountpoint can be satisfied by a stale mount that no longer
# works, so prove the filesystem actually answers before trusting it.
if ! mountpoint -q "$MOUNT" || ! touch "$MOUNT/.ficatlas-write-test" 2>/dev/null; then
  log "ERROR: $MOUNT is not a working, writable mount — refusing to write"
  [ "${WE_MOUNTED:-0}" = 1 ] && umount "$MOUNT" 2>/dev/null
  exit 1
fi
rm -f "$MOUNT/.ficatlas-write-test"

mkdir -p "$DEST_DIR" 2>/dev/null

# ── 4. Copy only if this dump is not already there ──────────────────────────
BASE=$(basename "$NEWEST")
if [ -f "$DEST_DIR/$BASE" ] && [ "$(stat -c%s "$DEST_DIR/$BASE")" = "$(stat -c%s "$NEWEST")" ]; then
  [ "${WE_MOUNTED:-0}" = 1 ] && umount "$MOUNT" 2>/dev/null
  exit 0                                  # already offsite, nothing to do
fi

log "copying $BASE ($(du -h "$NEWEST" | cut -f1)) to $DEST_DIR"
# Write to a temporary name first, then rename. A copy interrupted by the laptop
# sleeping mid-transfer would otherwise leave a truncated file sitting there
# looking exactly like a valid backup.
if cp "$NEWEST" "$DEST_DIR/.$BASE.part" 2>/dev/null &&
   [ "$(stat -c%s "$DEST_DIR/.$BASE.part")" = "$(stat -c%s "$NEWEST")" ] &&
   mv "$DEST_DIR/.$BASE.part" "$DEST_DIR/$BASE" 2>/dev/null; then
  log "offsite copy complete: $BASE"
  # Record the success LOCALLY. The share is unmounted most of the time, so
  # "when did an offsite copy last succeed?" is otherwise unanswerable without
  # the laptop present — and that question is the whole point of the watchdog
  # check. A stamp on the local disk can always be read.
  touch "$SRC_DIR/.offsite-stamp" 2>/dev/null || true
else
  log "ERROR: copy failed or was truncated (laptop slept mid-transfer?)"
  rm -f "$DEST_DIR/.$BASE.part" 2>/dev/null
  [ "${WE_MOUNTED:-0}" = 1 ] && umount "$MOUNT" 2>/dev/null
  exit 1
fi

# ── 5. Keep the share tidy ──────────────────────────────────────────────────
ls -1t "$DEST_DIR"/ficatlas-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  log "pruning offsite $(basename "$old")"; rm -f "$old"
done

# Leave the share as we found it, so the laptop is free to sleep.
[ "${WE_MOUNTED:-0}" = 1 ] && umount "$MOUNT" 2>/dev/null

log "done — $(ls -1 "$DEST_DIR"/ficatlas-*.dump 2>/dev/null | wc -l) dump(s) offsite"
