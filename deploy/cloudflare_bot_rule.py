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
        # THE AGENT BROWSERS AS A CLASS, not one product at a time.
        #
        # This started as Lightpanda alone, written for one name, and the name
        # is the cheapest thing about a scraper to change. Eleven days later the
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
                      # Lightpanda, folded in from a rule of its own. Same
                      # product-token match it had alone; the only difference is
                      # that it now sits behind `not cf.client.bot`, which
                      # exempts Cloudflare-VERIFIED crawlers. Lightpanda is not
                      # in that set and cannot join it by naming itself, so the
                      # block is unchanged in practice — and folding it is what
                      # freed the fifth slot for the Tencent fleet below. Its
                      # measurement is kept in the git history of this file.
                      'http.user_agent contains "Lightpanda" or '
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
                      'http.user_agent contains "Diffbot" or '
                      # THE FORGED BARE-ORIGIN REFERER, folded in here rather
                      # than given a rule of its own: this phase is capped at
                      # five on the current plan, and a sixth is refused
                      # outright ("6 out of 5"). It belongs with these anyway —
                      # same job, same `not cf.client.bot` guard.
                      #
                      # 2026-09-27, 04:00-11:00, over 24h of origin logs:
                      #
                      #   requests carrying Referer: https://ficatlas.com  47,792
                      #     distinct client addresses                      47,755
                      #     requests per address                             1.0008
                      #     of those addresses, IPv6                        37,999
                      #     assets, beacons or JS fetched by any of them          0
                      #     overlap with addresses sending the REAL referer       0
                      #
                      # 29,241 to `/`, 7,735 to `/takedown`, 4,879 to
                      # `/permissions`. The IPv6 sits in provider /32s —
                      # 2a10:7b00::, 2a10:7b01::, 2a12:f544::, 2a12:f545::,
                      # 2a13:f41::, 2a13:f42:: — ~1,100 addresses apiece, which
                      # is a proxy pool and not an audience.
                      #
                      # WHY THE REFERER AND NOT THE AGENT OR THE ADDRESSES. It
                      # rotated 65 user-agent strings in near-perfect round
                      # robin (nine of them between 5,888 and 6,101 requests
                      # each), so the agent is worthless; and 47,755 addresses
                      # across six allocations is the exact shape an IP rule
                      # cannot answer, as this file already says of Lightpanda.
                      # What it got wrong is the one header it chose to forge.
                      #
                      # A browser's Referer is a URL, and for a document at the
                      # root of this origin that URL is `https://ficatlas.com/`
                      # — WITH the trailing slash. The bare origin form is what
                      # you get from writing the header by hand. Both appear in
                      # the logs and they do not mix: 2,658 requests from 91
                      # addresses sent the real one, and those addresses also
                      # fetch /sw.js, /api/auth/me and the pageview beacon,
                      # because they are browsers. The 47,755 sending the bare
                      # form fetched not one asset between them.
                      #
                      # A forged header is free, so this stops working the day
                      # they add a slash. Worth having regardless: zero false
                      # positives against a full day of real traffic, and
                      # --remove reverses it.
                      'http.referer eq "https://ficatlas.com" or '
                      # THE TENCENT CLOUD FLEET. Folded in for the same reason
                      # the forged referer above is: this phase is capped at
                      # five and a sixth rule is refused outright, and a
                      # rotating address pool is already what this rule is for.
                      #
                      # Measured 2026-10-16 over ~27h of nginx log:
                      #
                      #     requests from 43.172/43.173        30,091   56% of all
                      #     distinct addresses                 1,603
                      #     requests per address               18.8
                      #     robots.txt fetches by it                0
                      #
                      # It began at 04:00 UTC and ran a flat 1,500-2,400
                      # requests/hour for the next seventeen hours; before that
                      # the range was absent from the logs entirely. It takes
                      # 20,052 requests into /story/<uuid>, and probes
                      # /api/auth/me, /api/settings, /permissions and /login on
                      # the way past — reconnaissance, not reading.
                      #
                      # WHAT MADE IT INVISIBLE IN THE PANEL. It EXECUTES
                      # JAVASCRIPT, so it fires the pageview beacon: 578
                      # "visitors" who each viewed exactly one /fandom/ page and
                      # nothing else, at a median of 61.7 seconds apart, walking
                      # bon-jovi-band -> boy-meets-world -> 15th-century-ce-rpf
                      # -> peter-pan-related-fandoms. Those 578 were 91% of the
                      # day's visitors. Real traffic never moved — it was ~208
                      # pageviews, in line with the 173-284 of the days either
                      # side. Only the crawl spiked.
                      #
                      # WHY THE ADDRESSES AND NOT THE AGENT, against this file's
                      # own preference for names. It rotated 65 ordinary Chrome
                      # strings in round robin, so there is no token to match —
                      # this is the exact case the file already calls out as one
                      # an agent rule cannot answer. But the addresses are NOT
                      # the usual objection: they are not a residential proxy
                      # pool of one-request-each, they are 1,603 addresses
                      # inside TWO consecutive /16s of one cloud provider, at
                      # 18.8 requests apiece. That is a block an IP rule can
                      # answer, and it is scoped to the two /16s actually
                      # observed rather than the whole /14, so the rest of the
                      # provider's space stays open.
                      #
                      # `not cf.client.bot` keeps the verified crawlers out of
                      # it, as everywhere else here. Zero real readers were on
                      # these addresses in the window: the only other traffic
                      # from the range was one crawler on 165.22, and the fleet
                      # fetched no assets, no /sw.js and no beacons other than
                      # the one per fandom document.
                      #
                      # If it moves to a third /16 the rule stops matching and
                      # the traffic panel's scraper flag is the alarm — the
                      # single-page /fandom/ visitor is the signature to look
                      # for.
                      'ip.src in {43.172.0.0/16 43.173.0.0/16})',
    },
    {
        "description": "Block ShapBot (Parallel Web Systems; ignores robots.txt; ~93% of origin)",
        # Parallel Web Systems' crawler, which builds a search index sold to AI
        # agents. Measured 2026-10-04 over ~3.5h of nginx log: 41,840 requests,
        # ~93% of everything the origin served, 38,523 of them story pages
        # (each one a second /api/stories fetch from the Next server), up to
        # ~210/minute from 9 addresses, ZERO robots.txt fetches, and attempts
        # on /export.epub. Cloudflare's maintained AI-bot list did not have it.
        # Published as honouring robots.txt; it never asked, so the edge is the
        # only place a refusal can land. `--remove` reverses it.
        "expression": '(http.user_agent contains "ShapBot")',
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
        # AND NOT SOMEBODY WHO CLICKED A SEARCH RESULT.
        #
        # This carve-out was added after measuring what the rule cost. Two
        # independent sources agree and neither is the dev traffic the first
        # look blamed:
        #
        #   own beacon, comparable periods either side of 8 Sep:
        #     sessions that opened a story page   75 -> 29   (-61%)
        #     of which development sessions        2 ->  4   (i.e. not this)
        #   the edge, successful /story/ loads per day:
        #     40,903 · 42,909 · 67,374 · 103,529  ->  145 · 547 · 441 · 406
        #
        # Almost all of that second collapse is the botnet, which is the point
        # of the rule. But the site's whole funnel ends at a story page — 104
        # sessions searched last month, 38 opened a result, 8 reached an
        # archive — and an interstitial sits exactly on the step where a reader
        # decides to go and read.
        #
        # A REFERRER FROM THIS SITE is what separates them, and it is the one
        # signal the measured botnet does not have: 19,705 addresses, one
        # request each, arriving cold on story URLs it already knew. A reader
        # arrives at a story page by clicking a search result, so their request
        # carries ficatlas.com and is now let through.
        #
        # Spoofable, and deliberately accepted. Forging it costs one header —
        # but doing so is a behaviour change we would SEE: successful /story/
        # loads at the edge would climb back into the tens of thousands, and
        # the traffic panel's scraper flag watches the same shape from the
        # other side. A defence that can be adapted to, with the adaptation
        # visible, is worth more here than one that also refuses the readers.
        #
        # Arrivals from a search engine to a story page are still challenged.
        # That is ~4 sessions a month against ~560,000 refused requests a day,
        # and it is the trade this carve-out deliberately does not make.
        "expression": 'starts_with(http.request.uri.path, "/story/") '
                      'and not starts_with(http.request.uri.path, "/story/offline-shell") '
                      'and not cf.client.bot '
                      'and not http.cookie contains "sat=" '
                      'and not http.referer contains "ficatlas.com"',
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
