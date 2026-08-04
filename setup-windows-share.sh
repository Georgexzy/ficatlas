#!/usr/bin/env bash
#
# ⚠ DO NOT PASTE THE CONTENTS OF THIS FILE INTO A TERMINAL.
#
# This script calls `exit`. Pasted into an interactive shell, that `exit` runs
# in YOUR shell and closes the window — which is exactly what happens, and it
# looks like a crash. Removing the interactive prompts was not enough; any
# script with `exit` in it has this property.
#
# Run the FILE instead. This single line is safe to paste, because it is one
# command that runs the script in its own subshell:
#
#     sudo bash /home/george/ficatlas/setup-windows-share.sh
#
# One-shot setup for the Windows backup share. Run it once, with sudo:
#
#     sudo bash /home/george/ficatlas/setup-windows-share.sh
#
# It does three things the current /etc/fstab line gets wrong:
#
#   1. Moves the SMB password out of /etc/fstab. That file is world-readable
#      (-rw-r--r--), so every user and every process on this machine can read
#      the password to a network share. It goes into a 0600 credentials file.
#
#   2. Adds `noauto,user`. Without `user`, mounting needs root, so nothing
#      automated can remount the share when the Windows box comes back. Without
#      `noauto`, boot stalls whenever that box is off.
#
#   3. Tries SMB versions newest-first. The existing line pins vers=2.1, which
#      modern Windows commonly refuses — the likeliest reason the mount has been
#      failing.
#
# Safe to re-run. Backs up /etc/fstab first and validates before finishing; if
# no SMB version works it restores the original and changes nothing.

set -uo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash $0"; exit 1; }

SHARE='//100.99.74.82/Mass\040Storage'
MOUNT=/mnt/windows
CRED=/etc/samba/ficatlas.cred
REAL_USER="${SUDO_USER:-george}"
UID_N=$(id -u "$REAL_USER"); GID_N=$(id -g "$REAL_USER")

# Take the existing credentials out of fstab rather than asking for them again.
SMB_USER="${SMB_USER:-$(grep -oP 'username=\K[^,\s]+' /etc/fstab | head -1)}"
SMB_PASS="${SMB_PASS:-$(grep -oP 'password=\K[^,\s]+' /etc/fstab | head -1)}"
# Deliberately NOT interactive. `read` consumes stdin, so if this script is
# pasted into a terminal rather than run from a file, a prompt here would
# swallow the remaining lines of the script as its answer and execute nothing.
# Being non-interactive is what makes copy-paste safe.
if [ -z "${SMB_USER:-}" ] || [ -z "${SMB_PASS:-}" ]; then
  echo "Could not find username=/password= in /etc/fstab."
  echo "Pass them instead:"
  echo "    sudo SMB_USER=someone SMB_PASS='secret' bash $0"
  exit 1
fi

echo "==> backing up /etc/fstab"
cp -a /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d-%H%M%S)"

echo "==> writing $CRED (0600)"
install -d -m 755 /etc/samba
umask 077
printf 'username=%s\npassword=%s\n' "$SMB_USER" "$SMB_PASS" > "$CRED"
chmod 600 "$CRED"

mkdir -p "$MOUNT"
umount "$MOUNT" 2>/dev/null || true

# Try versions newest-first; the first that mounts wins.
WORKING=""
for v in 3.1.1 3.0 2.1 2.0; do
  printf '==> trying SMB vers=%s ... ' "$v"
  if mount -t cifs "//100.99.74.82/Mass Storage" "$MOUNT" \
       -o "credentials=$CRED,iocharset=utf8,vers=$v,uid=$UID_N,gid=$GID_N,file_mode=0644,dir_mode=0755" \
       2>/tmp/cifs.err; then
    echo "mounted"; WORKING="$v"; break
  fi
  echo "failed ($(tr -d '\n' < /tmp/cifs.err | tail -c 90))"
done

if [ -z "$WORKING" ]; then
  echo
  echo "No SMB version worked. Nothing has been changed in /etc/fstab."
  echo "Check that the share is enabled on the Windows machine and that"
  echo "'$SMB_USER' can reach it. Last error:"
  cat /tmp/cifs.err
  exit 1
fi

echo "==> updating /etc/fstab to use the credentials file and vers=$WORKING"
# Drop any previous line for this mountpoint, then append the corrected one.
grep -v "[[:space:]]$MOUNT[[:space:]]" /etc/fstab > /tmp/fstab.new
printf '%s %s cifs credentials=%s,iocharset=utf8,vers=%s,sec=ntlmssp,noauto,user,uid=%s,gid=%s,file_mode=0644,dir_mode=0755 0 0\n' \
  "$SHARE" "$MOUNT" "$CRED" "$WORKING" "$UID_N" "$GID_N" >> /tmp/fstab.new
cp /tmp/fstab.new /etc/fstab

# Prove the new line is correct by remounting purely from fstab.
umount "$MOUNT" 2>/dev/null || true
if mount "$MOUNT"; then
  echo "==> verified: mounts cleanly from /etc/fstab"
else
  echo "!! the new fstab line did not mount. Restoring the previous fstab."
  cp -a "$(ls -1t /etc/fstab.bak.* | head -1)" /etc/fstab
  exit 1
fi

# Writable by the normal user, which is what backups actually need.
if sudo -u "$REAL_USER" test -w "$MOUNT"; then
  echo "==> $REAL_USER can write to $MOUNT"
else
  echo "!! $MOUNT is mounted but not writable by $REAL_USER — backups would fail."
fi

echo
echo "Done. SMB version $WORKING, password now in $CRED (0600)."
df -h "$MOUNT" | tail -1
echo
echo "Now tell Claude it is mounted and offsite backups will be wired up."
