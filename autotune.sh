#!/usr/bin/env bash
# FicAtlas dynamic resource autotuner.
#
# WHY THIS EXISTS
#   The compose file caps Postgres and the worker (cpus: 1.0, mem_limit) so a
#   bulk aggregate or import can never push the desktop into swap again. That
#   floor is worth keeping, but a static cap is wrong in the other direction:
#   when you are NOT using the PC, the machine is idle, and there is no reason
#   to leave the database pegged at one core. Those are exactly the hours a
#   crawl or index job could use the machine's spare capacity.
#
#   This script widens the CPU allowance when the host is comfortably idle and
#   tightens it back to the floor when the machine is under pressure, so the
#   protection only ever bites when it needs to. Memory limits stay fixed at the
#   compose floor (they are what stop the thrash); only CPU is tuned, which is
#   the lever that changes interactive responsiveness.
#
# HOW PRESSURE IS MEASURED
#   Primary signal is Linux PSI (pressure stall information) for memory, which
#   directly measures how often tasks are waiting on memory — the thing that was
#   pegging iowait and pushing the desktop into swap. Fallback when PSI is
#   absent: swap occupancy + system load.
#
# Run from cron:
#   */2 * * * * /home/george/ficatlas/autotune.sh >> /home/george/ficatlas/backups/autotune.log 2>&1

set -uo pipefail
cd "$(dirname "$0")"

DOCKER="${DOCKER:-docker}"
LOG="autotune"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

# ── thresholds (tune via env) ───────────────────────────────────────────────
# Memory PSI avg10 above this (percent) is "pressure". Below this is "idle".
PRESSURE_PSI="${PRESSURE_PSI:-20}"
# Swap occupancy above this fraction of total swap is also "pressure".
PRESSURE_SWAP_FRAC="${PRESSURE_SWAP_FRAC:-0.5}"
# System load above this many cores is overloaded regardless of PSI.
PRESSURE_LOAD_FRAC="${PRESSURE_LOAD_FRAC:-1.5}"
# Consecutive identical samples before we act (hysteresis, avoids flapping).
SETTLE="${AUTOTUNE_SETTLE:-2}"

CORES=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)

# CPU allowance (in cores) for the tuned services, idle vs pressured.
db_idle_cpus="${DB_IDLE_CPUS:-$(( CORES - 2 ))}"
db_idle_cpus=$(( db_idle_cpus < 1 ? 1 : db_idle_cpus ))
worker_idle_cpus="${WORKER_IDLE_CPUS:-$(( CORES > 3 ? 3 : CORES ))}"
FLOOR_CPUS="${FLOOR_CPUS:-1.0}"

# ── measure current pressure ────────────────────────────────────────────────
pressure=0
if [ -r /proc/pressure/memory ]; then
  # PSI "some avg10" line: "some avg10=39.42 avg60=30.17 avg300=21.70 total=..."
  avg10=$(awk '/^some/ { for (i=1;i<=NF;i++) if ($i ~ /^avg10=/) { gsub(/avg10=/,"",$i); print $i; exit } }' /proc/pressure/memory)
  if [ -n "$avg10" ]; then
    avg10i=${avg10%.*}
    [ "$avg10i" -ge "$PRESSURE_PSI" ] && pressure=1
  fi
else
  # Fallback: swap occupancy.
  read -r total used < <(free -m | awk '/^Swap:/ {print $2, $3}')
  if [ "${total:-0}" -gt 0 ]; then
    frac=$(awk -v u="$used" -v t="$total" 'BEGIN { print u/t }')
    [ "$(awk -v f="$frac" -v p="$PRESSURE_SWAP_FRAC" 'BEGIN { print (f>=p) ? 1 : 0 }')" = "1" ] && pressure=1
  fi
fi

# Load average over cores is an independent overload signal.
read -r load1 _ < /proc/loadavg
loadfrac=$(awk -v l="$load1" -v c="$CORES" 'BEGIN { print l/c }')
[ "$(awk -v f="$loadfrac" -v p="$PRESSURE_LOAD_FRAC" 'BEGIN { print (f>=p) ? 1 : 0 }')" = "1" ] && pressure=1

# ── hysteresis: act only after SETTLE consecutive samples in one state ──────
STATE_DIR="${AUTOTUNE_STATE:-${XDG_RUNTIME_DIR:-/tmp}/ficatlas-autotune}"
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/state"
prev="$(cat "$STATE_FILE" 2>/dev/null || echo idle)"
[ "$prev" != "idle" ] && prev="pressure"
cur=$([ "$pressure" = "1" ] && echo pressure || echo idle)
hits_file="$STATE_DIR/hits"
if [ "$prev" = "$cur" ]; then
  hits=$(($(cat "$hits_file" 2>/dev/null || echo 0) + 1))
else
  hits=1
fi
printf '%s\n' "$cur" > "$STATE_FILE"
printf '%s\n' "$hits" > "$hits_file"

if [ "$hits" -lt "$SETTLE" ]; then
  log "sample: $cur ($hits/$SETTLE) load=$load1/$CORES" 
  exit 0
fi

# ── apply the allowance that matches the settled state ─────────────────────
# Compare against the CURRENT allowance, not the prior state. A `docker compose
# up --build -d db` (or any container recreate) resets the cpu allowance back
# to the compose floor while the state file still says "idle", and the old
# `[ "$prev" = "idle" ] && exit 0` shortcut then permanently left the DB pegged
# at the floor. Idempotent `docker update` against the live value self-corrects.
target_db="$FLOOR_CPUS"
target_worker="$FLOOR_CPUS"
if [ "$cur" = "idle" ]; then
  target_db="$db_idle_cpus"
  target_worker="$worker_idle_cpus"
fi

actual_db="$(docker inspect -f '{{.HostConfig.NanoCpus}}' ficatlas-db-1 2>/dev/null || echo 0)"
actual_worker="$(docker inspect -f '{{.HostConfig.NanoCpus}}' ficatlas-worker-1 2>/dev/null || echo 0)"
want_db_nano=$(awk -v c="$target_db" 'BEGIN { printf "%d", c * 1000000000 }')
want_worker_nano=$(awk -v c="$target_worker" 'BEGIN { printf "%d", c * 1000000000 }')

if [ "$cur" = "pressure" ] && [ "${actual_db:-0}" = "$want_db_nano" ] \
   && [ "${actual_worker:-0}" = "$want_worker_nano" ]; then
  exit 0                                        # already throttled to the floor
fi
if [ "$cur" = "idle" ] && [ "${actual_db:-0}" = "$want_db_nano" ] \
   && [ "${actual_worker:-0}" = "$want_worker_nano" ]; then
  exit 0                                        # already wide open
fi

log "$cur settled — db to ${target_db}c, worker to ${target_worker}c (load=$load1/$CORES)"
"$DOCKER" update --cpus "$target_db" ficatlas-db-1 >/dev/null 2>&1
"$DOCKER" update --cpus "$target_worker" ficatlas-worker-1 >/dev/null 2>&1
