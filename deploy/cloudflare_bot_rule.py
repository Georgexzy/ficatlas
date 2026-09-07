#!/usr/bin/env python3
"""Block the crawler that will not read robots.txt.

    python3 deploy/cloudflare_bot_rule.py --dry-run
    python3 deploy/cloudflare_bot_rule.py

This is deliberately AT THE EDGE and not in robots.txt or nginx, and the
measurement is the argument for all three of those decisions. Over 24 hours of
origin logs (85,435 requests total):

    meta-webindexer   22,287 requests   26% of everything
      of which        14,187 to /?…     the search URL space
    robots.txt fetches by it:  0

So: it is the single largest consumer of this site, a quarter of it, and more
than half of what it asks for is the one space robots.txt explicitly disallows
because every URL in it is a query over 20.5M rows on a home server. It has
never once fetched robots.txt, which is why adding a line there would change
nothing — you cannot decline a request that is never made.

nginx would work, and would still carry every one of those requests down a
domestic connection through the tunnel before dropping it. Cloudflare refuses
them at the edge, which is the only layer that saves the bandwidth as well as
the query.

What is NOT blocked, and why the expression is this narrow:

  * `facebookexternalhit` is Meta's LINK PREVIEW fetcher, ~125 requests a day.
    It runs when somebody shares a ficatlas link on Facebook, Instagram or
    WhatsApp, and blocking it makes those shares render as a bare grey box.
    That is a reader telling their friends about the site; it is the opposite
    of the traffic this rule is about.
  * Applebot (13,410/day) and Amzn-SearchBot (3,300/day) stay. They cost more
    between them than Meta does, and they have a search product behind them
    that sends readers back — which is the same test that let SemrushBot be
    blocked and these two be kept. See the crawler notes in CLAUDE.md.

The token needs `Zone > Firewall Services > Edit` (or `Zone WAF > Edit`). The
one in .env is otherwise read-only by design.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.cloudflare.com/client/v4"
PHASE = "http_request_firewall_custom"

DESCRIPTION = "Block meta-webindexer (ignores robots.txt; 26% of origin traffic)"
# `contains` rather than an exact match: Meta sends this token inside five
# different browser-shaped user agents (Windows/Chrome, Mac/Chrome, Linux/Chrome,
# Edge, and one more), all of which appeared in the 24h sample. The product token
# is the stable part.
EXPRESSION = '(http.user_agent contains "meta-webindexer")'


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
        return {"success": False, "http": e.code,
                "errors": json.loads(e.read().decode() or "{}").get("errors")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true",
                    help="take the rule out again, leaving any others alone")
    args = ap.parse_args()

    e = env()
    tok = e.get("FICATLAS_CF_API_TOKEN")
    zone = e.get("FICATLAS_CF_ZONE_ID")
    if not tok or not zone:
        sys.exit("FICATLAS_CF_API_TOKEN and FICATLAS_CF_ZONE_ID must be set in .env")

    # The custom-firewall phase may not exist on a zone that has never had a
    # rule in it. Reading the entrypoint creates nothing and 404s cleanly, so
    # this works either way and never clobbers rules somebody added by hand:
    # whatever comes back is sent again with ours appended.
    current = call(tok, f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint")
    rules = []
    if current.get("success"):
        rules = [r for r in (current["result"].get("rules") or [])]
    kept = [r for r in rules if r.get("description") != DESCRIPTION]

    print("zone  :", zone)
    print("phase :", PHASE)
    print("rules already there:", len(kept))
    for r in kept:
        print("   keep:", str(r.get("description"))[:60])

    if args.remove:
        if len(kept) == len(rules):
            print("rule not present — nothing to remove")
            return 0
        payload = [{k: r[k] for k in ("action", "expression", "description", "enabled")
                    if k in r} for r in kept]
        if args.dry_run:
            print("\n--dry-run: would leave", len(payload), "rules")
            return 0
        out = call(tok, f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint", "PUT",
                   {"rules": payload})
        print("removed" if out.get("success") else f"failed: {out.get('errors')}")
        return 0 if out.get("success") else 1

    if len(kept) != len(rules):
        print("already present — nothing to do")
        return 0

    print("adding:", DESCRIPTION)
    print("match :", EXPRESSION)
    if args.dry_run:
        print("\n--dry-run, nothing sent")
        return 0

    payload = [{k: r[k] for k in ("action", "expression", "description", "enabled")
                if k in r} for r in kept]
    payload.append({
        "action": "block",
        "expression": EXPRESSION,
        "description": DESCRIPTION,
        "enabled": True,
    })
    out = call(tok, f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint", "PUT",
               {"rules": payload})
    if not out.get("success"):
        print("failed:", out.get("errors"))
        print("\nIf this is an authentication error the token lacks Firewall\n"
              "Services > Edit. Widen it while running this, or paste the\n"
              "expression above into Security > WAF > Custom rules.")
        return 1

    print("\ncreated. Verify:")
    print('  curl -sI -A "Mozilla/5.0 (compatible; meta-webindexer/1.1)" '
          'https://ficatlas.com/ | head -1        # expect 403')
    print('  curl -sI -A "facebookexternalhit/1.1" https://ficatlas.com/ | head -1'
          '   # must still be 200')
    return 0


if __name__ == "__main__":
    sys.exit(main())
