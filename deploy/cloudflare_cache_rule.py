#!/usr/bin/env python3
"""Create the Cloudflare cache rule for public content pages.

    python3 deploy/cloudflare_cache_rule.py --dry-run   # show what it would do
    python3 deploy/cloudflare_cache_rule.py

Why this exists as a script rather than a click in the dashboard: the rule is
half of a change whose other half is in this repo. `next.config.ts` sends
`s-maxage` on exactly these paths, and the rule's TTL mode is `respect_origin`,
so the two have to describe the same set of paths or the caching either does not
happen or happens somewhere it should not. Keeping the expression next to the
header keeps them honest.

WHAT IT DOES, and the reasoning, because a cache rule on a site with logins is
the sort of thing that should never be pasted in unread:

  * Caches only /story/, /series/, /fandom/, /ship/ and /s/. Never the home
    page, /library, /account, /settings, or any /api path.
  * Only when the request carries no `sat` cookie -- the same guard the existing
    "Cache anonymous search + facet reads" rule uses. This is belt and braces:
    nothing under frontend/app/ calls `cookies()`, so the server HTML is already
    identical for every visitor, and reader state arrives after hydration.
  * `respect_origin` for both TTLs, so the numbers live in next.config.ts and
    changing them is a deploy rather than a dashboard visit.

The measurement that motivated it: 1,032,799 edge requests over 30 days against
865 human pageviews, 6.7% cached, and 7,065 of the last ~12,000 requests were
/story/{id}. Those are crawlers walking ~750k story pages, every one of them
reaching a home server.

The API token needs `Zone > Cache Rules > Edit` in addition to the analytics
read this repo otherwise uses. Widen it only while running this, or use a
separate token -- the one in .env is deliberately read-only.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.cloudflare.com/client/v4"

def _anon(paths) -> str:
    """Match any of these path prefixes, for a reader with no session."""
    return ("(" + " or ".join(
        f'starts_with(http.request.uri.path, "{p}")' for p in paths
    ) + ') and not http.cookie contains "sat="')


# Every cache rule this repo owns, in one list, because the failure this file
# exists to prevent is a rule and its origin header drifting apart — and that
# gets likelier, not less likely, as rules accumulate in a dashboard nobody
# reads. Adding one here and running the script is the whole procedure.
RULES = [
    {
        "description": "Cache anonymous story/series/hub pages; respect origin TTL",
        "expression": _anon(("/story/", "/series/", "/fandom/", "/ship/", "/s/")),
        # Origin half: the headers() block in frontend/next.config.ts.
    },
    {
        # The header widget and the landing page ask for this on every page
        # load, and it is four counters that are already in memory. Measured
        # over 24h of origin logs: 20,372 requests, 24% of everything the
        # origin was asked for, from a single client — every one of them down
        # the tunnel to a home server. Nothing about the answer varies by
        # reader, so the edge can hold it.
        #
        # Origin half: total_stats() in backend/api/stats.py, which sends
        # max-age=300 to match both the server's own recompute cycle and the
        # client-side TTL in lib/api.ts. respect_origin below means that header
        # is the setting and this rule only grants permission.
        "description": "Cache anonymous index totals; respect origin TTL",
        "expression": _anon(("/api/stats/totals",)),
    },
]

# Kept for the message printed at the end.
DESCRIPTION = RULES[0]["description"]


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
    args = ap.parse_args()

    e = env()
    tok = e.get("FICATLAS_CF_API_TOKEN")
    zone = e.get("FICATLAS_CF_ZONE_ID")
    if not tok or not zone:
        sys.exit("FICATLAS_CF_API_TOKEN and FICATLAS_CF_ZONE_ID must be set in .env")

    sets = call(tok, f"/zones/{zone}/rulesets")
    if not sets.get("success"):
        sys.exit(f"cannot read rulesets: {sets.get('errors')}")
    cache_sets = [r for r in sets["result"]
                  if r.get("phase") == "http_request_cache_settings"]
    if not cache_sets:
        sys.exit("no http_request_cache_settings ruleset on this zone")
    rs_id = cache_sets[0]["id"]

    existing = call(tok, f"/zones/{zone}/rulesets/{rs_id}")
    have = {r.get("description") for r in
            ((existing.get("result") or {}).get("rules") or [])}

    print("ruleset :", rs_id)
    todo = [r for r in RULES if r["description"] not in have]
    for r in RULES:
        print(f"  [{'present' if r['description'] in have else ' add   '}] {r['description']}")
        if r["description"] not in have:
            print("            ", r["expression"])
    if not todo:
        print("nothing to do")
        return 0
    if args.dry_run:
        print("\n--dry-run, nothing sent")
        return 0

    for rule in todo:
        r = call(tok, f"/zones/{zone}/rulesets/{rs_id}/rules", "POST", {
            "description": rule["description"],
            "expression": rule["expression"],
            "action": "set_cache_settings",
            "action_parameters": {
                "cache": True,
                "edge_ttl": {"mode": "respect_origin"},
                "browser_ttl": {"mode": "respect_origin"},
            },
            "enabled": True,
        })
        if not r.get("success"):
            print("failed:", rule["description"], r.get("errors"))
            print("\nIf this is an authentication error the token is read-only, which is\n"
                  "how it is meant to be. Add Zone > Cache Rules > Edit, or create the\n"
                  "rule in the dashboard with the expression printed above.")
            return 1
        print("created:", rule["description"])

    print("\nVerify with two requests to the same URL:")
    print("  curl -sI https://ficatlas.com/story/<id>   | grep -i cf-cache-status")
    print("  curl -sI https://ficatlas.com/api/stats/totals | grep -i cf-cache-status")
    print("the second of each should say HIT. With a `sat` cookie it must NOT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
