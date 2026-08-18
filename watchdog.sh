#!/usr/bin/env bash
# FicAtlas watchdog — checks the stack is actually serving, restarts what is not,
# and reports upward so that silence is itself an alarm.
#
# Run from cron every few minutes:
#   */5 * * * * /home/george/ficatlas/watchdog.sh >> /home/george/ficatlas/backups/watchdog.log 2>&1
#
# Two halves, because they catch different failures:
#
#   LOCAL   Container up but not answering, database not accepting connections,
#           a worker that died. This half can fix things: it restarts the
#           specific service that is unhealthy.
#
#   REMOTE  A heartbeat ping to an external service after a successful check.
#           This is the half that catches the failures the local half cannot
#           report — power cut, internet down, the machine itself gone. A
#           watchdog running on the box that died cannot tell you it died, so
#           the alert has to be the ABSENCE of a ping, not the presence of one.
#
# For the remote half, set HEARTBEAT_URL to a check URL from any dead-man's-
# switch service (healthchecks.io and cronitor both have free tiers, and
# Uptime Kuma has a push monitor if you would rather self-host elsewhere). Put
# it in .env as HEARTBEAT_URL=... — it is read from there automatically.
#
# Deliberately has no notification logic of its own. Sending mail from a home
# IP is unreliable, and the alerting is exactly the part that must not depend on
# this machine still working.

set -uo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a

HEARTBEAT_URL="${HEARTBEAT_URL:-}"
API="${WATCHDOG_API:-http://127.0.0.1:8000/health}"
WEB="${WATCHDOG_WEB:-http://127.0.0.1:3000/}"
TIMEOUT="${WATCHDOG_TIMEOUT:-20}"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

problems=()
fixed=()

log() { echo "$STAMP  $*"; }

check_http() {                       # name url  -> 0 ok / 1 bad
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$2" 2>/dev/null)
  [ "$code" = "200" ] && return 0
  problems+=("$1 returned HTTP ${code:-none}")
  return 1
}

restart() {                          # service reason
  log "restarting $1 ($2)"
  if docker compose restart "$1" >/dev/null 2>&1; then
    fixed+=("restarted $1")
  else
    problems+=("could not restart $1")
  fi
}

# ── containers ──────────────────────────────────────────────────────────────
for svc in db backend frontend worker; do
  state=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null \
          | awk -v s="$svc" '$1==s {print $2}')
  if [ -z "$state" ]; then
    problems+=("$svc is not running")
    restart "$svc" "not running"
  elif [ "$state" != "running" ]; then
    problems+=("$svc is $state")
    restart "$svc" "state=$state"
  fi
done

# ── database accepting connections ──────────────────────────────────────────
if ! docker compose exec -T db pg_isready -U ficatlas -d ficatlas >/dev/null 2>&1; then
  problems+=("postgres not accepting connections")
  restart db "pg_isready failed"
  sleep 20
fi

# ── the two things a visitor actually touches ───────────────────────────────
# Checked after the container checks so a restart above has had a chance to help.
# If anything was restarted above, give it time to bind its port first. Without
# this the HTTP check runs against a container that is still starting, fails,
# and restarts the same service a second time — pointless, and slower to recover
# than simply waiting.
if [ ${#fixed[@]} -gt 0 ]; then
  log "waiting ${WATCHDOG_SETTLE:-30}s for restarted service(s) to come up"
  sleep "${WATCHDOG_SETTLE:-30}"
fi

check_http "api" "$API"  || restart backend "health check failed"
check_http "web" "$WEB"  || restart frontend "home page failed"

# Re-check once after any restart, so the heartbeat reflects reality rather than
# the state we found on arrival.
if [ ${#fixed[@]} -gt 0 ]; then
  sleep 25
  problems=()
  check_http "api" "$API" || true
  check_http "web" "$WEB" || true
fi

# ── disk, because a full disk takes everything down quietly ─────────────────
free_mb=$(df -Pm . | tail -1 | awk '{print $4}')
if [ "$free_mb" -lt "${WATCHDOG_MIN_FREE_MB:-5000}" ]; then
  problems+=("only ${free_mb}MB free on disk")
fi

# ── backups, because a backup that quietly stopped is only discovered when it
#    is needed ────────────────────────────────────────────────────────────────
# Both halves are age checks on things that should move on their own. Neither
# can be answered by "did the script exit 0" — the nightly dump and the offsite
# copy both run from cron into a log nobody reads, and backup-offsite.sh exits 0
# on purpose when the target laptop is off, which is the common case. So the
# only honest signal is staleness.
newest_dump=$(ls -1t backups/ficatlas-essential-*.dump 2>/dev/null | head -1)
if [ -z "$newest_dump" ]; then
  problems+=("no local backup exists at all")
else
  dump_age_h=$(( ( $(date +%s) - $(stat -c %Y "$newest_dump") ) / 3600 ))
  # The dump runs nightly, so 48h means at least one run was missed outright.
  if [ "$dump_age_h" -gt "${WATCHDOG_BACKUP_MAX_AGE_H:-48}" ]; then
    problems+=("newest local backup is ${dump_age_h}h old")
  fi
fi

# The offsite target is a laptop that is legitimately off for days at a time, so
# this threshold is deliberately loose. It is not asking "did it copy last
# night" — it is asking whether the only copy that survives losing this machine
# has drifted far enough to be worth knowing about.
if [ -f backups/.offsite-stamp ]; then
  offsite_age_d=$(( ( $(date +%s) - $(stat -c %Y backups/.offsite-stamp) ) / 86400 ))
  if [ "$offsite_age_d" -gt "${WATCHDOG_OFFSITE_MAX_AGE_D:-7}" ]; then
    problems+=("last offsite backup copy was ${offsite_age_d} days ago")
  fi
fi

# ── report ──────────────────────────────────────────────────────────────────
if [ ${#fixed[@]} -gt 0 ]; then log "actions: ${fixed[*]}"; fi

if [ ${#problems[@]} -eq 0 ]; then
  # Only ping when genuinely healthy. Pinging regardless would turn the
  # dead-man's switch into a liveness check for cron, which is not the question.
  if [ -n "$HEARTBEAT_URL" ]; then
    curl -fsS -m 10 --retry 2 "$HEARTBEAT_URL" >/dev/null 2>&1 \
      || log "WARN: heartbeat ping failed (external service or network down)"
  fi
  # Quiet on success unless something was repaired — a log that prints every
  # five minutes is a log nobody reads.
  [ ${#fixed[@]} -gt 0 ] && log "healthy again after repair"
  exit 0
fi

log "UNHEALTHY: ${problems[*]}"
# Deliberately do NOT ping on failure: the alert is the missing heartbeat.
if [ -n "$HEARTBEAT_URL" ]; then
  curl -fsS -m 10 "${HEARTBEAT_URL}/fail" >/dev/null 2>&1 || true
fi
exit 1
