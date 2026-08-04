#!/usr/bin/env bash
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
  # Delete by comment so we only ever remove our own rules.
  while iptables -L DOCKER-USER --line-numbers -n 2>/dev/null | grep -q "$COMMENT"; do
    local n
    n=$(iptables -L DOCKER-USER --line-numbers -n | grep "$COMMENT" | head -1 | awk '{print $1}')
    iptables -D DOCKER-USER "$n" || break
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
for net in 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12 169.254.0.0/16 100.64.0.0/10; do
  iptables -I DOCKER-USER 1 -d "$net" -j DROP -m comment --comment "$COMMENT" \
    && echo "    deny  -> $net"
done

# Established replies, so outbound internet connections still function.
iptables -I DOCKER-USER 1 -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN \
  -m comment --comment "$COMMENT" && echo "    allow established replies"

# Docker's own subnets last, so they sit at the very top and win over the drops.
while read -r sub; do
  [ -n "$sub" ] || continue
  iptables -I DOCKER-USER 1 -s "$sub" -d "$sub" -j RETURN \
    -m comment --comment "$COMMENT" && echo "    allow container-to-container $sub"
done < <(docker_subnets)

# Boot-time re-apply: the rules are installed above, and at boot the containers
# may not be up yet, so verifying against them would fail spuriously and roll
# back the very rules we are trying to restore.
if [ "${1:-}" = "--apply-only" ]; then
  echo "rules applied (boot mode, verification skipped)"
  exit 0
fi

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
