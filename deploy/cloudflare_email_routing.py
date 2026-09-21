#!/usr/bin/env python3
"""Forward the addresses this site publishes, instead of dropping them.

    python3 deploy/cloudflare_email_routing.py --dry-run
    python3 deploy/cloudflare_email_routing.py

Email Routing was enabled on ficatlas.com and its only rule was
`all -> drop`: every message to every address at the domain was discarded,
silently. The site publishes addresses — the takedown page, the permissions
page and the README all tell authors to write in — so anybody who did was
talking to nothing.

WHAT THIS IS NOT. Email Routing is INBOUND only. It forwards mail TO a verified
destination; it cannot send mail FROM the domain, so it is not what makes
password reset work. That needs an SMTP credential (see password_reset.py) and
is a separate thing entirely, which is worth stating because "I set up email on
Cloudflare" and "the app can send email" sound like the same sentence.

The catch-all stays `drop`. A domain that accepts anything at any address
collects spam for ever; the named addresses below are the ones actually
published, and mail to `xyz@ficatlas.com` should go nowhere.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.cloudflare.com/client/v4"

# The addresses the site actually publishes, and where they go. Adding one here
# and running this is the whole procedure — the same argument the WAF rule
# script makes for keeping its rules in one list.
FORWARD = ["help", "admin", "takedown", "permissions", "abuse", "postmaster"]


def env() -> dict:
    f = ROOT / ".env"
    if not f.exists():
        sys.exit(".env not found")
    return dict(l.strip().split("=", 1) for l in f.read_text().splitlines()
                if "=" in l and not l.lstrip().startswith("#"))


def call(tok, path, method="GET", body=None):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "http": e.code}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--to", default="", help="destination; defaults to the "
                                             "account's verified address")
    args = ap.parse_args()

    e = env()
    tok, zone = e.get("FICATLAS_CF_API_TOKEN"), e.get("FICATLAS_CF_ZONE_ID")
    if not tok or not zone:
        sys.exit("FICATLAS_CF_API_TOKEN and FICATLAS_CF_ZONE_ID must be in .env")

    acct = call(tok, "/zones")["result"][0]["account"]["id"]
    dests = [d for d in (call(tok, f"/accounts/{acct}/email/routing/addresses")
                         .get("result") or []) if d.get("verified")]
    if not dests:
        sys.exit("No VERIFIED destination address on the account. Add one in "
                 "the dashboard and click the link Cloudflare emails you — "
                 "Cloudflare will not forward to an unverified address.")
    to = args.to or dests[0]["email"]
    if to not in [d["email"] for d in dests]:
        sys.exit(f"{to} is not a verified destination on this account")

    existing = call(tok, f"/zones/{zone}/email/routing/rules").get("result") or []
    have = {m.get("value"): r for r in existing
            for m in (r.get("matchers") or []) if m.get("type") == "literal"}

    print(f"zone : {zone}\nto   : {to}")
    for name in FORWARD:
        addr = f"{name}@{e.get('FICATLAS_DOMAIN', 'ficatlas.com')}"
        cur = have.get(addr)
        if cur:
            goes = [a.get("value", [None])[0] for a in (cur.get("actions") or [])]
            if to in goes:
                print(f"  [present] {addr} -> {to}")
                continue
        print(f"  [{'would add' if args.dry_run else ' add     '}] {addr} -> {to}")
        if args.dry_run:
            continue
        body = {
            "name": f"forward {addr}",
            "enabled": True,
            "matchers": [{"type": "literal", "field": "to", "value": addr}],
            "actions": [{"type": "forward", "value": [to]}],
        }
        r = (call(tok, f"/zones/{zone}/email/routing/rules/{cur['tag']}", "PUT", body)
             if cur else
             call(tok, f"/zones/{zone}/email/routing/rules", "POST", body))
        if not r.get("success"):
            print(f"            FAILED: {r.get('errors') or r.get('http')}")

    if args.dry_run:
        print("\n--dry-run, nothing sent")
    else:
        print("\nDone. The catch-all is deliberately left dropping — see the "
              "module note.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
