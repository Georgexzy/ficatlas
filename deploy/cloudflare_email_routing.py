#!/usr/bin/env python3
"""Give ficatlas.com an inbox, and forward it somewhere a person reads.

    python3 deploy/cloudflare_email_routing.py --to you@gmail.com --dry-run
    python3 deploy/cloudflare_email_routing.py --to you@gmail.com

Why it is needed: /about tells authors "There is no contact address yet — this
runs on a home machine and has no domain of its own — so the form is the way to
reach whoever maintains it." That was true when it was written and has not been
for months. An author who wants their work removed should not have to use a web
form because the site never got round to having an address.

Why Cloudflare Email Routing rather than a mailbox: this site is behind a tunnel
on a domestic connection, and running an SMTP server there means an MX record
pointing at a home IP — unreliable to receive on and a standing invitation. The
routing service takes delivery at Cloudflare and forwards to an address that
already works, which costs nothing and adds no service to keep running.

RECEIVING is what this sets up. Sending is a separate decision with its own
trade-offs — see the note at the bottom of this file and
backend/api/password_reset.py, which already works without any of it.

What it does, in order:

  1. enables Email Routing on the zone, which creates the MX and SPF records
     Cloudflare needs to take delivery;
  2. adds the forwarding destination — and this is the step that needs a human,
     because Cloudflare sends it a verification link that has to be clicked
     before anything can be forwarded there. That is not a limitation to work
     around: it is what stops anyone pointing a domain's mail at your inbox;
  3. creates one rule per address.

Safe to re-run: every step checks for what it is about to create.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.cloudflare.com/client/v4"

# The addresses the site should answer on. `help` is what a reader needs and
# `admin` is what another operator or a registrar would try; both land in the
# same place, so this is two doors into one room rather than two inboxes to
# watch. Deliberately not `noreply@` — a site that asks authors to trust it
# should not write to them from an address that refuses replies.
ADDRESSES = ("help", "admin")


def env() -> dict:
    f = ROOT / ".env"
    if not f.exists():
        sys.exit(".env not found")
    return dict(l.strip().split("=", 1) for l in f.read_text().splitlines()
                if "=" in l and not l.lstrip().startswith("#"))


def call(tok, path, method="GET", body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"success": False, "http": e.code, **json.loads(e.read().decode() or "{}")}
        except Exception:
            return {"success": False, "http": e.code}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="where mail should be forwarded")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    e = env()
    tok, zone = e.get("FICATLAS_CF_API_TOKEN"), e.get("FICATLAS_CF_ZONE_ID")
    if not tok or not zone:
        sys.exit("FICATLAS_CF_API_TOKEN and FICATLAS_CF_ZONE_ID must be set in .env")

    z = call(tok, f"/zones/{zone}")
    if not z.get("success"):
        sys.exit(f"cannot read the zone: {z.get('errors')}")
    domain = z["result"]["name"]
    account = z["result"]["account"]["id"]

    status = call(tok, f"/zones/{zone}/email/routing")
    enabled = bool((status.get("result") or {}).get("enabled"))
    print(f"domain        : {domain}")
    print(f"routing       : {'already enabled' if enabled else 'OFF — will enable'}")
    print(f"forward to    : {args.to}")
    print(f"addresses     : {', '.join(a + '@' + domain for a in ADDRESSES)}")

    if args.dry_run:
        print("\n--dry-run, nothing sent")
        return 0

    if not enabled:
        r = call(tok, f"/zones/{zone}/email/routing/enable", "POST", {})
        if not r.get("success"):
            print("could not enable routing:", r.get("errors"))
            print("\nThe token needs Zone > Email Routing > Edit. Add it while\n"
                  "running this, or turn routing on in the dashboard under\n"
                  "Email > Email Routing, then re-run for the rules.")
            return 1
        print("enabled routing (MX and SPF records created)")

    # The destination has to verify itself. Adding it twice is harmless and the
    # API says so rather than erroring, so this is safe to re-run while waiting
    # for the click.
    dest = call(tok, f"/accounts/{account}/email/routing/addresses", "POST",
                {"email": args.to})
    if dest.get("success"):
        verified = (dest.get("result") or {}).get("verified")
        print("destination   :", "already verified" if verified
              else "ADDED — check that inbox and click the verification link")
    else:
        msgs = [m.get("message", "") for m in (dest.get("errors") or [])]
        if any("already exists" in m for m in msgs):
            print("destination   : already added")
        else:
            print("destination   : could not add:", msgs[:2])

    existing = call(tok, f"/zones/{zone}/email/routing/rules")
    have = set()
    for rule in (existing.get("result") or []):
        for m in rule.get("matchers") or []:
            if m.get("field") == "to":
                have.add((m.get("value") or "").lower())

    for local in ADDRESSES:
        addr = f"{local}@{domain}"
        if addr in have:
            print(f"rule          : {addr} already routed")
            continue
        r = call(tok, f"/zones/{zone}/email/routing/rules", "POST", {
            "actions": [{"type": "forward", "value": [args.to]}],
            "matchers": [{"field": "to", "type": "literal", "value": addr}],
            "enabled": True,
            "name": f"forward {addr}",
        })
        print(f"rule          : {addr} -> {args.to}"
              if r.get("success") else
              f"rule          : {addr} FAILED {r.get('errors')}")

    print("\nMail will not be delivered until the destination is verified —")
    print("Cloudflare will have emailed a link to", args.to)
    print("\nSENDING is separate and this does not turn it on. The reset flow in")
    print("backend/api/password_reset.py already works without it (it creates a")
    print("code an operator passes on). To make it automatic, set SMTP_HOST,")
    print("SMTP_PORT, SMTP_USER, SMTP_PASS and SMTP_FROM in .env for any relay")
    print("that will accept mail from this domain — no code change is needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
