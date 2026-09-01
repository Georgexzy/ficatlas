"""What Cloudflare saw, which is the half of the traffic this site cannot see.

`tracking.py` records a pageview when a BROWSER renders one and reports it with
a beacon. That is a deliberate choice and its docstring explains why: taking
pageviews off the server's own request log means keeping more about each request
than this site wants to keep, and separating a person from an asset fetch out of
that log is not reliable.

The cost of that choice is stated there too, and it is a large one for a site
whose whole growth plan is being indexed: **crawlers are not counted at all,
because crawlers do not run JavaScript.** So the one question that matters most
early on — "is Google actually crawling us?" — is exactly the question the
built-in analytics cannot answer.

Cloudflare already counts every request that reaches the edge, crawlers and
assets included, because it is the thing serving them. Reading its analytics
costs no new storage here, adds no column to `visit_events`, and records nothing
about anybody: it is a count that already exists, fetched read-only.

The two are NOT the same measurement and the panel must not add them up:

    Cloudflare requests   every HTTP request at the edge — pages, JSON, images,
                          fonts, crawlers, health checks
    beacon pageviews      one per page a human's browser actually rendered

The interesting figure is the RELATIONSHIP between them, not either alone. A
large gap is mostly assets and crawlers; a gap that grows while beacon views
stay flat is a crawler discovering the site, which is the thing you are waiting
for.

Configuration — both are needed, and neither is the tunnel token:

    CLOUDFLARE_API_TOKEN   a token with Zone > Analytics > Read, from
                           dash.cloudflare.com > My Profile > API Tokens
    CLOUDFLARE_ZONE_ID     dash.cloudflare.com > the domain > Overview,
                           bottom right

Unset, this module is inert and the panel says so rather than showing an empty
chart that looks like "no traffic".
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

log = logging.getLogger("cloudflare_analytics")

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

# Cloudflare's daily rollups settle over minutes, not seconds, and the admin
# page is refreshed by hand. A short cache turns a page reload into zero
# outbound calls, which matters because this runs INSIDE a request: the
# autopoll incident in CLAUDE.md was an endpoint that awaited a third party
# with no bound on how long that could take.
CACHE_SECONDS = float(os.getenv("CF_ANALYTICS_CACHE_SEC", "600"))

# Deliberately short. A slow answer here must degrade to "Cloudflare did not
# answer in time" long before nginx's proxy_read_timeout 60s turns it into a
# 500 with nothing in the API log.
TIMEOUT = float(os.getenv("CF_ANALYTICS_TIMEOUT_SEC", "8"))

_lock = threading.Lock()
_cache: dict[int, tuple[float, dict]] = {}

# httpRequests1dGroups is the daily zone rollup and is available on every plan,
# which matters: the adaptive datasets that carry a bot score are Bot Management
# features and would make this work only on a paid plan. Bot classification is
# therefore NOT attempted here -- see the module docstring for what is inferred
# from the request/pageview gap instead, which is honest on any plan.
QUERY = """
query FicAtlasTraffic($zone: String!, $since: Date!, $until: Date!) {
  viewer {
    zones(filter: {zoneTag: $zone}) {
      httpRequests1dGroups(
        limit: 366
        filter: {date_geq: $since, date_leq: $until}
        orderBy: [date_ASC]
      ) {
        dimensions { date }
        sum {
          requests
          pageViews
          cachedRequests
          bytes
          threats
          countryMap { clientCountryName requests }
        }
        uniq { uniques }
      }
    }
  }
}
"""


def _missing() -> list[str]:
    """Which of the two is absent, named.

    Both are needed and either can arrive on its own: compose interpolates from
    the host shell as well as .env, so a stray CLOUDFLARE_API_TOKEN in an
    operator's environment half-configures this without anyone setting it here.
    Saying "not connected" in that state sends someone looking for a token they
    already have.
    """
    return [k for k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ZONE_ID")
            if not (os.getenv(k) or "").strip()]


def configured() -> bool:
    return not _missing()


def _post(token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch(days: int = 30) -> dict:
    """Daily edge totals, or a dict saying why there are none.

    Never raises. The admin panel has to render either way, and a traffic page
    that 500s because a third party is slow is worse than one that says so.
    """
    missing = _missing()
    if missing:
        return {"configured": False, "missing": missing,
                "reason": f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set"}

    now = time.monotonic()
    with _lock:
        hit = _cache.get(days)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]

    until = date.today()
    since = until - timedelta(days=days - 1)
    try:
        body = _post(os.getenv("CLOUDFLARE_API_TOKEN"), {
            "query": QUERY,
            "variables": {"zone": os.getenv("CLOUDFLARE_ZONE_ID"),
                          "since": since.isoformat(), "until": until.isoformat()},
        })
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
        out = {"configured": True, "error": f"HTTP {e.code}", "detail": detail}
        log.warning("cloudflare analytics: HTTP %s", e.code)
        return out
    except Exception as e:
        # Includes the timeout. Named, because "no data" and "we gave up after
        # eight seconds" are different things to see on an admin page.
        return {"configured": True, "error": type(e).__name__, "detail": str(e)[:300]}

    # GraphQL answers 200 with an `errors` array, so a failure here looks like a
    # success to anything checking the status code. The message is passed
    # through verbatim: a schema that has moved on says so precisely, and
    # guessing at field names from outside is how this stays broken quietly.
    if body.get("errors"):
        msg = "; ".join(str(e.get("message", e))[:200] for e in body["errors"][:3])
        log.warning("cloudflare analytics: %s", msg)
        # The most likely failure by far, and Cloudflare states it as an actor
        # id and a permission string that mean nothing to the person reading an
        # admin page. It happens because token permissions REPLACE rather than
        # add: granting a token Cache Rules Edit (to apply the caching rule in
        # deploy/cloudflare_cache_rule.py) drops Analytics Read unless both are
        # ticked, and the traffic page then goes blank with a sentence nobody
        # can act on.
        if "analytics.read" in msg or "analytics" in msg.lower():
            return {"configured": True,
                    "error": "The API token cannot read analytics",
                    "fix": "Add Zone > Analytics > Read to the token at "
                           "dash.cloudflare.com > My Profile > API Tokens. "
                           "Permissions replace rather than add, so tick it "
                           "alongside anything else the token needs.",
                    "detail": msg}
        return {"configured": True, "error": "GraphQL error", "detail": msg}

    try:
        zones = body["data"]["viewer"]["zones"]
    except (KeyError, TypeError):
        return {"configured": True, "error": "Unexpected response shape",
                "detail": json.dumps(body)[:300]}
    if not zones:
        return {"configured": True, "error": "Zone not found",
                "detail": "CLOUDFLARE_ZONE_ID does not match a zone this token can read"}

    groups = zones[0].get("httpRequests1dGroups") or []
    out = _shape(groups)
    out["configured"] = True
    with _lock:
        _cache[days] = (time.monotonic(), out)
    return out


def _shape(groups: list) -> dict:
    days_out, countries = [], {}
    totals = {"requests": 0, "page_views": 0, "cached": 0,
              "bytes": 0, "threats": 0, "uniques": 0}

    for g in groups:
        s = g.get("sum") or {}
        u = g.get("uniq") or {}
        day = (g.get("dimensions") or {}).get("date")
        days_out.append({
            "day": day,
            "requests": s.get("requests", 0),
            "page_views": s.get("pageViews", 0),
            "cached": s.get("cachedRequests", 0),
            "uniques": u.get("uniques", 0),
        })
        totals["requests"] += s.get("requests", 0) or 0
        totals["page_views"] += s.get("pageViews", 0) or 0
        totals["cached"] += s.get("cachedRequests", 0) or 0
        totals["bytes"] += s.get("bytes", 0) or 0
        totals["threats"] += s.get("threats", 0) or 0
        # Uniques are per-day and do not add up across days, for the same reason
        # visit_events' visitor hash does not: the same person on two days is
        # two uniques. Summed here it would be a "visitors" figure that grows
        # with the window rather than with the audience, so it is reported as a
        # peak day instead.
        totals["uniques"] = max(totals["uniques"], u.get("uniques", 0) or 0)
        for c in (s.get("countryMap") or []):
            name = c.get("clientCountryName") or "??"
            countries[name] = countries.get(name, 0) + (c.get("requests") or 0)

    top_countries = sorted(countries.items(), key=lambda kv: -kv[1])[:12]
    cache_ratio = (totals["cached"] / totals["requests"]) if totals["requests"] else None

    return {
        "days": days_out,
        "totals": totals,
        "busiest_uniques": totals["uniques"],
        "cache_ratio": cache_ratio,
        "countries": [{"country": c, "requests": n} for c, n in top_countries],
    }
