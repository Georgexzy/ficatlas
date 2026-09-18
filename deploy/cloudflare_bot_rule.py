#!/usr/bin/env python3
"""Block, at the edge, what robots.txt cannot make stop.

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
  * Applebot and Amzn-SearchBot are still not blocked as agents, and the second
    rule below is narrower than it first looks: it refuses them /story/* and
    nothing else. Both keep the home page, both index pages and all 11,196 hub
    pages. See the group for each in robots.txt for the measurement and the
    reasoning; this rule exists only because Applebot has demonstrated it does
    not take the whole of that file's word for it. Amzn-SearchBot honours
    Crawl-delay to the tenth of a second and will almost certainly never reach
    this rule; it is named for symmetry with the file that states the policy.

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

# Every WAF rule this repo owns, in one list, for the same reason the cache
# script keeps one: rules accumulate in a dashboard nobody reads, and the only
# defence is that adding one here and running this is the whole procedure.
RULES = [
    {
        "description": "Block Lightpanda (agent browser; 98.3% of origin traffic)",
        # An AGENT BROWSER — a real engine built to be driven by scrapers and
        # AI agents. Measured over 24 hours of origin logs:
        #
        #     Lightpanda/1.0   87,330 of 88,860 requests   98.3%
        #     peak hour                43,645 requests     ~12/second
        #     distinct IPv6 addresses   5,863, across five /32 allocations
        #     robots.txt fetches               0
        #
        # It walks the whole site — /, /fandoms, /ships, /about, /permissions —
        # and follows the author link on every result card, which is how it
        # produced 11,632 requests into `/api/search`, each one a query over
        # 20.5M rows on a home connection.
        #
        # It EXECUTES JAVASCRIPT, which is what made it invisible: it fires the
        # pageview beacon, so it recorded 23,993 searches and 23,984
        # "visitors" — one search each — and every one counted as a reader in
        # the traffic panel. The `_NOT_A_BROWSER` heuristic cannot see it,
        # because that looks for "searched and never rendered a page".
        #
        # At the edge and not in robots.txt for the reason the meta-webindexer
        # rule gives: it has never fetched robots.txt, and you cannot decline a
        # request that is never preceded by asking. Not in nginx either,
        # because 87,000 requests would still come down the tunnel first.
        #
        # The product token only. Rotating 5,863 addresses across five
        # allocations is exactly what an IP rule cannot answer, and the agent
        # names itself honestly — which is worth keeping true by not making
        # the name the thing that gets it blocked in a way that rewards lying.
        "expression": '(http.user_agent contains "Lightpanda")',
    },
    {
        # THE AGENT BROWSERS AS A CLASS, not one product at a time.
        #
        # The Lightpanda rule above was written for one name, and the name is
        # the cheapest thing about a scraper to change. Eleven days later the
        # traffic panel's new scraper flag went off on this:
        #
        #     19:30:39  /ship/byun-baekhyun-do-kyungsoo-d-o
        #     19:30:49  /fandom/avengers
        #     19:31:01  /ship/total-drama-presents-the-ridonculous-race
        #     19:31:16  /ship/corvo-attano-the-outsider-dishonored
        #     19:31:22  /ship/taylor-swift-travis-kelce
        #     19:31:41  /ship/alejandro-vargas-reader
        #
        # Six unrelated hub pages in sixty-two seconds, no referrer on any of
        # them, and A DIFFERENT VISITOR HASH FOR EVERY ONE — the hash is IP
        # plus user agent, so that is a rotating address pool. Every request
        # fired the pageview beacon, so it executes JavaScript and is a real
        # engine rather than a fetch loop. The same signature as the Lightpanda
        # scrape, under a different name.
        #
        # So this names the CLASS. Every token here is one the application
        # already treats as automation (tracking._BOT_RE), and not one of them
        # is a string a reader's browser sends:
        #
        #   Headless     Chrome's own headless mode and every driver's default
        #   Playwright, Puppeteer, Selenium, WebDriver, PhantomJS
        #                browser automation, announcing itself
        #   Browserless, Browserbase, AgentQL, Scrapfly, ScrapingBee, ZenRows,
        #   BrightData, Apify, Crawlee, Firecrawl, Jina-AI, Diffbot
        #                commercial scraping-as-a-service and agent browsers
        #
        # `not cf.client.bot` for the same reason the /story/ rule carries it:
        # it exempts Cloudflare-VERIFIED crawlers, so no amount of string
        # matching here can ever cost us Googlebot or bingbot. Nothing in the
        # list resembles their user agents, and the carve-out costs nothing.
        #
        # The one thing this can plausibly catch that is wanted: PageSpeed
        # Insights has shipped a `HeadlessChrome` user agent in the past
        # (current builds say `Chrome-Lighthouse`). If a performance audit
        # starts failing, that is why, and `--remove` reverses the lot.
        "description": "Block agent browsers and scraping services (rotating pools)",
        "expression": '(not cf.client.bot) and ('
                      'http.user_agent contains "Headless" or '
                      'http.user_agent contains "Playwright" or '
                      'http.user_agent contains "Puppeteer" or '
                      'http.user_agent contains "Selenium" or '
                      'http.user_agent contains "WebDriver" or '
                      'http.user_agent contains "PhantomJS" or '
                      'http.user_agent contains "Browserless" or '
                      'http.user_agent contains "Browserbase" or '
                      'http.user_agent contains "AgentQL" or '
                      'http.user_agent contains "Scrapfly" or '
                      'http.user_agent contains "ScrapingBee" or '
                      'http.user_agent contains "ZenRows" or '
                      'http.user_agent contains "BrightData" or '
                      'http.user_agent contains "Apify" or '
                      'http.user_agent contains "Crawlee" or '
                      'http.user_agent contains "Firecrawl" or '
                      'http.user_agent contains "Jina-AI" or '
                      'http.user_agent contains "Diffbot")',
    },
    {
        "description": "Block meta-webindexer (ignores robots.txt; 26% of origin traffic)",
        # `contains` rather than an exact match: Meta sends this token inside
        # five different browser-shaped user agents (Windows/Chrome, Mac/Chrome,
        # Linux/Chrome, Edge, and one more), all of which appeared in the 24h
        # sample. The product token is the stable part.
        "expression": '(http.user_agent contains "meta-webindexer")',
    },
    {
        # Enforcement for the Applebot and Amzn-SearchBot groups in robots.txt.
        # That file is where the policy is stated and where the numbers are
        # written down; this is what makes it true for an agent that reads the
        # file and then does its own thing anyway.
        #
        # Applebot has form for exactly that. robots.txt has carried
        # `Crawl-delay: 10` throughout, Amzn-SearchBot obeys it to the tenth of
        # a second (measured: one request every 10.00s), and Applebot arrived
        # every 1.38s in the same window — 25,394 requests in 9.7h, 98.6% of
        # them into /story/*, which is a 20.5M-page space it can never finish
        # and whose every URL is a cache miss down the tunnel to a home server.
        #
        # Path-scoped on purpose. This is not a block on either crawler: the
        # home page, /fandoms, /ships and all 11,196 hub pages stay open to
        # both, and those are the pages the site actually wants indexed. Only
        # the unbounded tail is refused.
        #
        # `Applebot` also matches `Applebot-Extended`, which is fine — that is
        # an AI opt-out token with no crawler behind it, so it never makes a
        # request for this to refuse.
        "description": "Refuse Applebot/Amzn-SearchBot the 20.5M-page /story/ tail",
        "expression": '(http.user_agent contains "Applebot" or '
                      'http.user_agent contains "Amzn-SearchBot") and '
                      'starts_with(http.request.uri.path, "/story/")',
    },
    {
        # The residential-proxy botnet. This one is a CHALLENGE and not a block,
        # and it is the only rule here aimed at something that cannot be named.
        #
        # Measured 2026-09-08 over 9.7h: 20,221 requests to /story/* from 19,705
        # UNIQUE IPs — one request per address — cycling 14 browser user-agent
        # strings in near-perfect round robin (1,353 to 1,465 requests each) for
        # 20,032 distinct story URLs. Blocking by IP is what a proxy network of
        # that shape exists to defeat, and blocking by user agent means blocking
        # ordinary Chrome strings that real readers also send.
        #
        # What it cannot fake cheaply is a browser. It fetched 20,221 story
        # pages and 138 static assets — 0.7% — so it is reading the server-
        # rendered HTML and never executing the page. A managed challenge is
        # exactly that test, which is why it is the instrument here.
        #
        # The scope is narrow because the measurement allows it to be: this
        # botnet made ZERO requests to any path outside /story/. Three carve-
        # outs keep it off everyone it is not aimed at.
        #
        #   * `not cf.client.bot` exempts Cloudflare-VERIFIED crawlers, which is
        #     Googlebot and bingbot. Getting this wrong would be the worst
        #     outcome available — Googlebot is at 2 requests a day and a
        #     challenge it cannot solve would take that to zero.
        #   * `sat=` exempts anyone signed in.
        #   * /story/offline-shell is the service worker's precache target (100
        #     requests in the window). A service worker fetch cannot solve a
        #     challenge, so covering it would break offline mode for real
        #     readers.
        #
        # Next's client-side navigation is covered on purpose, and this is the
        # one decision here worth arguing with. Clicking a search result fetches
        # `/story/<id>?_rsc=<hash>`, and a fetch() cannot solve a challenge — so
        # the obvious kindness is to exempt `_rsc`. Do not: the botnet ALREADY
        # sends it, 223 of its 20,221 requests, and an exemption is a bypass
        # that costs one query parameter to use. What happens instead is
        # tolerable: the RSC fetch gets challenge HTML rather than a flight
        # payload, Next falls back to a full navigation, and the reader answers
        # one challenge as a document. Cloudflare then honours the cf_clearance
        # cookie on everything after it, RSC fetches included, without this rule
        # having to say so.
        #
        # The cost, stated plainly: an anonymous reader arriving on a story page
        # from a search engine passes a managed challenge first. For a real
        # browser that is usually invisible and under a second. It is a genuine
        # cost against ~50,000 scraped pages a day, and it is reversible with
        # `--remove` the moment it stops being worth it.
        # NOT HERE: a managed challenge on the search space, `/?…`.
        #
        # It was written, dry-run, and deliberately not applied. It would work
        # — a rotating pool cannot cheaply answer one — and the cost lands on
        # exactly the wrong people: `/?…` is where every shared search link
        # arrives, including the ones the outreach panel builds for a reply on
        # Reddit. The whole point of that link is that somebody clicks it and
        # sees results; an interstitial is the opposite.
        #
        # The /story/ rule below accepts that cost because 100% of the traffic
        # it refused was a botnet and story pages are not where readers are
        # won. Search is the product.
        #
        # If a scraper returns that Cloudflare's managed AI-bot blocking does
        # not know, this is the next lever — and it should be scoped to `/?…`
        # and never to `/api/search`, because challenge HTML handed to a
        # reader's XHR cannot be answered by it.
        "description": "Challenge the /story/ scraper botnet (19,705 IPs, 1 req each)",
        "action": "managed_challenge",
        "expression": 'starts_with(http.request.uri.path, "/story/") '
                      'and not starts_with(http.request.uri.path, "/story/offline-shell") '
                      'and not cf.client.bot '
                      'and not http.cookie contains "sat="',
    },
]


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


def _wire(r: dict) -> dict:
    """A live rule reduced to the fields the PUT accepts."""
    return {k: r[k] for k in ("action", "expression", "description", "enabled")
            if k in r}


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
    # The custom-firewall phase may not exist on a zone that has never had a
    # rule in it. Reading the entrypoint creates nothing and 404s cleanly, so
    # this works either way and never clobbers rules somebody added by hand:
    # whatever comes back is sent again with ours reconciled into it.
    current = call(tok, f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint")
    rules = list((current.get("result") or {}).get("rules") or []) \
        if current.get("success") else []
    ours = {r["description"] for r in RULES}
    foreign = [r for r in rules if r.get("description") not in ours]
    live = {r.get("description"): r for r in rules if r.get("description") in ours}

    print("zone  :", zone)
    print("phase :", PHASE)
    for r in foreign:
        print("   keep (not ours):", str(r.get("description"))[:60])

    if args.remove:
        if not live:
            print("none of our rules are present — nothing to remove")
            return 0
        payload = [_wire(r) for r in foreign]
        if args.dry_run:
            print("\n--dry-run: would leave", len(payload), "rules")
            return 0
        out = call(tok, f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint", "PUT",
                   {"rules": payload})
        print("removed" if out.get("success") else f"failed: {out.get('errors')}")
        return 0 if out.get("success") else 1

    # Reconciled on the EXPRESSION, not merely on whether the name is there.
    # The cache-rule script had the name-only bug and it meant widening a rule
    # silently did nothing; the same mistake here would be worse, because the
    # thing that silently does nothing is a block.
    changed = False
    for r in RULES:
        cur = live.get(r["description"])
        if cur is None:
            state = " add   "; changed = True
        elif ((cur.get("expression") or "").strip() != r["expression"].strip()
              or cur.get("action") != r.get("action", "block")):
            state = " update"; changed = True
        else:
            state = "present"
        print(f"  [{state}] {r['description']}")
        if state != "present":
            print("             action:", r.get("action", "block"))
            print("            ", r["expression"])
            if cur:
                print("             was:", (cur.get("expression") or "").strip())

    if not changed:
        print("nothing to do")
        return 0
    if args.dry_run:
        print("\n--dry-run, nothing sent")
        return 0

    # Foreign rules first, ours after, in the order RULES declares them — the
    # phase is evaluated top-down and rebuilding it wholesale is the only write
    # this API offers, so the order has to be reconstructed deliberately rather
    # than inherited from whatever came back.
    payload = [_wire(r) for r in foreign] + [{
        "action": r.get("action", "block"),
        "expression": r["expression"],
        "description": r["description"],
        "enabled": True,
    } for r in RULES]

    out = call(tok, f"/zones/{zone}/rulesets/phases/{PHASE}/entrypoint", "PUT",
               {"rules": payload})
    if not out.get("success"):
        print("failed:", out.get("errors"))
        print("\nIf this is an authentication error the token lacks Firewall\n"
              "Services > Edit. Widen it while running this, or paste the\n"
              "expressions above into Security > WAF > Custom rules.")
        return 1

    print("\ncreated/updated. Verify:")
    print('  curl -sI -A "Mozilla/5.0 (compatible; meta-webindexer/1.1)" '
          'https://ficatlas.com/ | head -1                 # expect 403')
    print('  curl -sI -A "... (Applebot/0.1)" https://ficatlas.com/story/<id> | head -1'
          '   # expect 403')
    print('  curl -sI -A "... (Applebot/0.1)" https://ficatlas.com/ships | head -1'
          '        # must still be 200')
    print('  curl -sI -A "facebookexternalhit/1.1" https://ficatlas.com/ | head -1'
          '        # must still be 200')
    return 0


if __name__ == "__main__":
    sys.exit(main())
