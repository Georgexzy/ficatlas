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
#     sudo bash /home/george/ficatlas/harden-network.sh
#
# Stop FicAtlas containers from reaching the rest of your home network.
#
#     sudo bash /home/george/ficatlas/harden-network.sh
#     sudo bash /home/george/ficatlas/harden-network.sh --undo
#
# WHY THIS MATTERS MORE THAN THE USUAL HARDENING
#
# Measured from inside the running backend container:
#
#     REACHABLE  router            192.168.1.1:80
#     REACHABLE  this host (SSH)   192.168.1.250:22
#     REACHABLE  internet          1.1.1.1:443
#
# That is Docker's default, not a misconfiguration — containers route through
# the host and nothing stops them addressing the LAN. It means any flaw that
# lets an attacker make requests from inside a container (a container escape,
# but equally an SSRF in the app) reaches your router's admin page, this
# machine's SSH port, and every other device on 192.168.1.0/24.
#
# The tunnel does not help here. It protects the inbound direction — no open
# ports, home IP never in DNS. This is the outbound direction, and it is the
# one that turns "someone broke the website" into "someone is on the home
# network".
#
# WHAT THIS DOES
#
# Adds rules to DOCKER-USER, which Docker evaluates before its own chains and
# leaves alone across restarts:
#
#   1. container -> container on FicAtlas's own bridge      ALLOW
#   2. replies to connections the container started          ALLOW
#   3. container -> 192.168/16, 10/8, 172.16/12, 169.254/16  DROP
#      container -> 100.64/10 (Tailscale CGNAT)              DROP
#   4. everything else (the public internet)                 ALLOW
#
# Rule 4 has to stay: the crawlers need AO3, FanFiction.net and archive.org.
# This is about denying the private ranges, not egress in general.
#
# Docker's own bridge subnets are excluded from rule 3, or containers could not
# talk to each other or to the DNS resolver Docker provides.
#
# Safe: reversible with --undo, verifies the stack still works afterwards, and
# rolls back automatically if it does not.

set -uo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash $0"; exit 1; }

COMMENT="ficatlas-egress"
UNIT=/etc/systemd/system/ficatlas-firewall.service
SELF="$(readlink -f "$0")"

# Every subnet Docker is currently using — these must stay reachable.
docker_subnets() {
  docker network ls -q 2>/dev/null | while read -r n; do
    docker network inspect "$n" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null
  done | tr ' ' '\n' | grep -E '^[0-9]' | sort -u
}

flush_rules() {
  # Delete by comment so we only ever remove our own rules. Both chains:
  # DOCKER-USER for traffic routed onward, INPUT for traffic aimed at this host.
  local chain n
  for chain in DOCKER-USER INPUT; do
    while iptables -L "$chain" --line-numbers -n 2>/dev/null | grep -q "$COMMENT"; do
      n=$(iptables -L "$chain" --line-numbers -n | grep "$COMMENT" | head -1 | awk '{print $1}')
      iptables -D "$chain" "$n" || break
    done
  done
}

if [ "${1:-}" = "--undo" ]; then
  echo "==> removing $COMMENT rules"
  flush_rules
  systemctl disable --now ficatlas-firewall.service 2>/dev/null || true
  rm -f "$UNIT"; systemctl daemon-reload 2>/dev/null || true
  echo "Done. Containers can reach the LAN again."
  iptables -L DOCKER-USER -n --line-numbers | head -20
  exit 0
fi

command -v iptables >/dev/null || { echo "iptables not found"; exit 1; }
iptables -L DOCKER-USER -n >/dev/null 2>&1 || {
  echo "DOCKER-USER chain missing — is Docker running?"; exit 1; }

echo "==> clearing any previous $COMMENT rules"
flush_rules

# Inserted in reverse order because -I puts each at the top: the LAST insert
# ends up FIRST, and the allow rules must be evaluated before the drops.
echo "==> installing egress rules"
# Every rule is scoped to -s <docker subnet>. That is the correction, and the
# reason matters because I got this wrong twice.
#
# The first version dropped any packet to a private range. The second added
# --ctstate NEW. Both still had no SOURCE restriction, and Docker's own networks
# live inside 172.16.0.0/12 — so a phone opening the site matched
# "-d 172.16.0.0/12, state NEW" and was dropped on the way IN. The rules were
# blocking inbound traffic to my own containers, which is why Tailscale access
# died both times while every check I ran from the host still passed.
#
# Scoping by source makes that impossible by construction rather than by
# ordering or state: traffic from a phone is not from a Docker subnet, so it
# cannot match these rules at all, no matter where they sit in the chain. The
# only thing they can affect is a connection a CONTAINER opens.
#
# --ctstate NEW stays as well, so replies to connections the host or a visitor
# established are never candidates either.
echo "==> installing egress rules"
while read -r sub; do
  [ -n "$sub" ] || continue
  # Container to container on its own bridge stays allowed.
  iptables -I DOCKER-USER 1 -s "$sub" -d "$sub" -j RETURN \
    -m comment --comment "$COMMENT"
  for net in 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12 169.254.0.0/16 100.64.0.0/10; do
    iptables -A DOCKER-USER -s "$sub" -d "$net" -m conntrack --ctstate NEW -j DROP \
      -m comment --comment "$COMMENT"
  done
  echo "    $sub may not open connections to private networks"
done < <(docker_subnets)


# ── The host itself ─────────────────────────────────────────────────────────
# DOCKER-USER lives in the FORWARD chain, which only sees traffic being routed
# THROUGH the host to somewhere else. Traffic from a container to one of the
# host's OWN addresses terminates locally and goes to INPUT instead, so none of
# the rules above touch it.
#
# Measured after the first version of this script: the router was correctly
# blocked and 192.168.1.250:22 — SSH on this machine — was still wide open from
# inside the container. That is the more valuable target of the two, so the
# script was giving a false sense of security.
#
# Containers need nothing from the host: Docker gives them their own resolver
# inside the container namespace, and they reach Postgres over the bridge
# network, not via a host address.
echo "==> blocking container -> this host"
while read -r sub; do
  [ -n "$sub" ] || continue
  # Allow the bridge gateway only, which is the container's default route and
  # the path to the internet; everything else on the host is refused.
  # -I, not -A. Appending puts these at the BOTTOM of INPUT, and on this host
  # an earlier ACCEPT matched first — the rules were installed, reported success
  # and did nothing, which the verification step caught: "host SSH from
  # container: STILL REACHABLE".
  #
  # Inserted in reverse so the final order is ACCEPT-established then DROP:
  # -I always goes to position 1, so whatever is inserted LAST ends up FIRST.
  # Same correction: only connections the container itself opens. Replies to
  # something the host started (docker-proxy forwarding a visitor's request into
  # the container) must not be caught.
  iptables -I INPUT 1 -s "$sub" -m conntrack --ctstate NEW -j DROP \
    -m comment --comment "$COMMENT" \
    && echo "    deny new $sub -> host services"
done < <(docker_subnets)

# Boot-time re-apply: the rules are installed above, and at boot the containers
# may not be up yet, so verifying against them would fail spuriously and roll
# back the very rules we are trying to restore.
if [ "${1:-}" = "--apply-only" ]; then
  echo "rules applied (boot mode, verification skipped)"
  exit 0
fi

# Invariant check, worth more here than any connectivity probe.
#
# Both previous failures had the same shape: a DROP rule with no source
# restriction, which therefore also matched traffic arriving AT the containers.
# And both times the connectivity checks passed, because a request from this
# host to its own address never traverses FORWARD — only a real external client
# does, and there is no way to simulate one from here.
#
# So assert the property directly instead: every rule we install must name a
# source. If one does not, it can affect inbound traffic and must not ship.
echo "==> checking every rule is source-scoped"
UNSCOPED=$(iptables -S DOCKER-USER 2>/dev/null | grep -- "--comment $COMMENT" | grep -v -- "-s " | wc -l)
UNSCOPED=$((UNSCOPED + $(iptables -S INPUT 2>/dev/null | grep -- "--comment $COMMENT" | grep -v -- "-s " | wc -l)))
if [ "$UNSCOPED" -ne 0 ]; then
  echo "!! $UNSCOPED rule(s) have no source restriction — these would block traffic"
  echo "   arriving at containers, not just traffic leaving them. Rolling back."
  flush_rules
  exit 1
fi
echo "    all rules restricted to container sources"

echo
echo "==> verifying the stack still works"
sleep 3
FAIL=0
cd "$(dirname "$SELF")"
for probe in "http://127.0.0.1:8000/health" "http://127.0.0.1:3000/"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$probe")
  echo "    $probe -> ${code:-none}"
  [ "$code" = "200" ] || FAIL=1
done
# The crawlers must still reach the outside world.
if docker compose exec -T backend python -c "
import socket,sys
s=socket.socket(); s.settimeout(8)
try: s.connect(('1.1.1.1',443)); print('    internet from container: OK')
except Exception as e: print('    internet from container: FAILED', type(e).__name__); sys.exit(1)
" 2>/dev/null; then :; else FAIL=1; fi

# The host itself — the case the first version of this script missed entirely.
if docker compose exec -T backend python -c "
import socket,sys
s=socket.socket(); s.settimeout(5)
try:
    s.connect(('192.168.1.250',22)); print('    host SSH from container: STILL REACHABLE'); sys.exit(1)
except Exception: print('    host SSH from container: blocked')
" 2>/dev/null; then :; else
  echo "    (host still reachable — INPUT rules did not take effect)"; FAIL=1
fi

# Inbound access must survive. This is the check that was missing when the first
# version "passed" and had in fact cut off every Tailscale client.
TS_IP=$(tailscale ip -4 2>/dev/null | head -1)
if [ -n "$TS_IP" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://$TS_IP:3000/")
  echo "    site over Tailscale ($TS_IP:3000) -> ${code:-none}"
  [ "$code" = "200" ] || FAIL=1
fi

# And the LAN must now be unreachable — the entire point.
if docker compose exec -T backend python -c "
import socket,sys
s=socket.socket(); s.settimeout(5)
try:
    s.connect(('192.168.1.1',80)); print('    LAN from container: STILL REACHABLE'); sys.exit(1)
except Exception: print('    LAN from container: blocked')
" 2>/dev/null; then :; else
  echo "    (LAN still reachable — rules did not take effect)"; FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  echo
  echo "!! verification failed — rolling back so nothing is left broken."
  flush_rules
  exit 1
fi

# iptables rules do not survive a reboot. Reapply after Docker starts.
echo
echo "==> installing $UNIT so the rules survive reboots"
cat > "$UNIT" <<UNITEOF
[Unit]
Description=FicAtlas container egress rules
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash $SELF --apply-only

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl daemon-reload
systemctl enable ficatlas-firewall.service >/dev/null 2>&1

echo
echo "Done. Containers can still reach the internet; they can no longer reach"
echo "your router, this host's other services, or anything else on the LAN."
echo "Undo at any time with:  sudo bash $SELF --undo"
