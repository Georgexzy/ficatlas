#!/usr/bin/env python3
"""Cloudflare's own AI-scraper blocking, which is the layer that does not rot.

    python3 deploy/cloudflare_bot_settings.py --show
    python3 deploy/cloudflare_bot_settings.py

Why this exists
---------------
A user-agent list always lags. This site has learned it three times — first
`python-urllib` missing from the bot regex, then a botnet rotating fourteen
browser strings, then `Lightpanda`, an AI-agent browser that was **98.3% of a
day's origin traffic** and counted as 23,984 readers because it executes
JavaScript and fires the pageview beacon.

Naming the fourth one after it arrives is not a defence, it is a postmortem.
Cloudflare maintains signatures and TLS fingerprints for this whole class and
updates them without anybody here noticing, which is the only part of this
that keeps working while unattended.

What is turned on, and what is not
----------------------------------
  ai_bots_protection  = block     the maintained AI-scraper class
  crawler_protection  = enabled   Cloudflare's wider unwanted-crawler category
  fight_mode          = OFF       deliberately

`fight_mode` is Bot Fight Mode, and it is left off on purpose: it challenges
anything it finds plausible, cannot be scoped to a path, and the people it
inconveniences are readers arriving from a link. The rules in
`cloudflare_bot_rule.py` are scoped and measured; this is the maintained list;
Bot Fight Mode is neither.

Per-IP rate limiting is not here either, and the measurement is why: the scrape
came from **5,914 addresses averaging 14.9 requests each**. No per-IP limit can
see that, and the app's own limiter never came close to firing.
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

API = "https://api.cloudflare.com/client/v4"
WANT = {"ai_bots_protection": "block", "crawler_protection": "enabled"}


def _env() -> dict:
    out = {}
    p = pathlib.Path(__file__).resolve().parent.parent / ".env"
    for line in p.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> int:
    e = _env()
    tok, zone = e.get("FICATLAS_CF_API_TOKEN"), e.get("FICATLAS_CF_ZONE_ID")
    if not tok or not zone:
        sys.exit("FICATLAS_CF_API_TOKEN and FICATLAS_CF_ZONE_ID must be set in .env")
    url = f"{API}/zones/{zone}/bot_management"
    hdr = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

    cur = json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers=hdr)))["result"]
    print("current:")
    for k in sorted(WANT | {"fight_mode": None}):
        print(f"  {k:22} {cur.get(k)}")
    if "--show" in sys.argv:
        return 0
    if all(cur.get(k) == v for k, v in WANT.items()):
        print("nothing to do")
        return 0
    got = json.load(urllib.request.urlopen(urllib.request.Request(
        url, data=json.dumps(WANT).encode(), headers=hdr, method="PUT")))
    if not got.get("success"):
        sys.exit(f"failed: {got.get('errors')}")
    print("updated:")
    for k in sorted(WANT):
        print(f"  {k:22} {got['result'].get(k)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
