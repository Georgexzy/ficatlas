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
from datetime import datetime, timedelta

log = logging.getLogger("cloudflare_analytics")

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
ACCOUNTS_URL = "https://api.cloudflare.com/client/v4/accounts"

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
_account_id: str | None = None

# ACCOUNT-scoped rather than zone-scoped, which is not a stylistic choice.
#
# Cloudflare splits these into two separate permissions -- "Zone > Analytics >
# Read" and the account-level one -- and a token can easily hold one without the
# other. Measured on this installation: the zone dataset answered
#
#   Actor '...' does not have permission
#   'com.cloudflare.api.account.zone.analytics.read' for zone ...
#
# while the identical question asked through `accounts(...)` with a zoneTag
# filter returned data immediately. Asking the way the token can answer beats
# telling the operator to go back to the dashboard.
#
# httpRequestsAdaptiveGroups also carries more than the daily rollup did:
# cacheStatus, clientRequestPath and edgeResponseStatus are all here, and those
# are the three that say something the site's own beacon structurally cannot --
# what the edge served without asking us, what crawlers actually fetch, and
# what proportion of it failed. It has no `uniq{uniques}`, so unique visitors
# are simply not reported rather than being faked from something else.
QUERY = """
query FicAtlasEdge($account: String!, $zone: String!, $since: Time!, $until: Time!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      daily: httpRequestsAdaptiveGroups(
        limit: 400, orderBy: [date_ASC]
        filter: {datetime_geq: $since, datetime_leq: $until, zoneTag: $zone}
      ) { count dimensions { date } sum { edgeResponseBytes } }

      cache: httpRequestsAdaptiveGroups(
        limit: 20, orderBy: [count_DESC]
        filter: {datetime_geq: $since, datetime_leq: $until, zoneTag: $zone}
      ) { count dimensions { cacheStatus } }

      countries: httpRequestsAdaptiveGroups(
        limit: 12, orderBy: [count_DESC]
        filter: {datetime_geq: $since, datetime_leq: $until, zoneTag: $zone}
      ) { count dimensions { clientCountryName } }

      statuses: httpRequestsAdaptiveGroups(
        limit: 12, orderBy: [count_DESC]
        filter: {datetime_geq: $since, datetime_leq: $until, zoneTag: $zone}
      ) { count dimensions { edgeResponseStatus } }

      paths: httpRequestsAdaptiveGroups(
        limit: 15, orderBy: [count_DESC]
        filter: {datetime_geq: $since, datetime_leq: $until, zoneTag: $zone}
      ) { count dimensions { clientRequestPath } }
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


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


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


def _account(token: str) -> str | None:
    """The account the zone belongs to, discovered once and remembered.

    Not configuration: asking for it costs one call and one fewer thing for an
    operator to paste in wrongly.
    """
    global _account_id
    if _account_id:
        return _account_id
    try:
        body = _get(ACCOUNTS_URL, token)
        results = body.get("result") or []
        if results:
            _account_id = results[0]["id"]
    except Exception:
        return None
    return _account_id


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

    token = os.getenv("CLOUDFLARE_API_TOKEN")
    account = _account(token)
    if not account:
        return {"configured": True, "error": "The API token cannot list accounts",
                "fix": "Give the token Account > Account Analytics > Read, or "
                       "Zone > Analytics > Read, at dash.cloudflare.com > My "
                       "Profile > API Tokens. Permissions replace rather than "
                       "add, so tick it alongside anything else the token needs."}

    until = datetime.utcnow().replace(microsecond=0)
    since = (until - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0)
    try:
        body = _post(token, {
            "query": QUERY,
            "variables": {"account": account, "zone": os.getenv("CLOUDFLARE_ZONE_ID"),
                          "since": since.isoformat() + "Z",
                          "until": until.isoformat() + "Z"},
        })
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
        log.warning("cloudflare analytics: HTTP %s", e.code)
        return {"configured": True, "error": f"HTTP {e.code}", "detail": detail}
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
        if "analytics" in msg.lower() and "permission" in msg.lower():
            return {"configured": True,
                    "error": "The API token cannot read analytics",
                    "fix": "Add Account > Account Analytics > Read (or Zone > "
                           "Analytics > Read) at dash.cloudflare.com > My "
                           "Profile > API Tokens. Permissions replace rather "
                           "than add, so tick it alongside anything else the "
                           "token needs.",
                    "detail": msg}
        return {"configured": True, "error": "GraphQL error", "detail": msg}

    try:
        accounts = body["data"]["viewer"]["accounts"]
    except (KeyError, TypeError):
        return {"configured": True, "error": "Unexpected response shape",
                "detail": json.dumps(body)[:300]}
    if not accounts:
        return {"configured": True, "error": "No data for that zone",
                "detail": "The token reached Cloudflare but the zone returned nothing"}

    out = _shape(accounts[0])
    out["configured"] = True
    with _lock:
        _cache[days] = (time.monotonic(), out)
    return out


def _rows(block, key):
    return [(g["dimensions"][key], g["count"]) for g in (block or [])
            if g.get("dimensions", {}).get(key) is not None]


def _shape(a: dict) -> dict:
    daily = a.get("daily") or []
    days_out = [{"day": g["dimensions"]["date"], "requests": g["count"],
                 "bytes": (g.get("sum") or {}).get("edgeResponseBytes", 0)}
                for g in daily]

    requests = sum(g["count"] for g in daily)
    total_bytes = sum((g.get("sum") or {}).get("edgeResponseBytes", 0) or 0 for g in daily)

    # Cloudflare reports a cacheStatus per request: hit, miss, bypass, dynamic,
    # expired and a few others. Only `hit` was actually answered without asking
    # this server, which is the number worth showing -- lumping expired or
    # revalidated in with it would flatter the figure.
    cache = dict(_rows(a.get("cache"), "cacheStatus"))
    hits = cache.get("hit", 0)

    statuses = _rows(a.get("statuses"), "edgeResponseStatus")
    errors = sum(n for code, n in statuses if isinstance(code, int) and code >= 500)
    client_errors = sum(n for code, n in statuses if isinstance(code, int) and 400 <= code < 500)

    return {
        "days": days_out,
        "totals": {
            "requests": requests,
            "bytes": total_bytes,
            "cache_hits": hits,
            "server_errors": errors,
            "client_errors": client_errors,
        },
        "cache_ratio": (hits / requests) if requests else None,
        "cache_breakdown": [{"status": k, "requests": v}
                            for k, v in sorted(cache.items(), key=lambda kv: -kv[1])],
        "countries": [{"country": c, "requests": n}
                      for c, n in _rows(a.get("countries"), "clientCountryName")],
        "statuses": [{"status": c, "requests": n} for c, n in statuses],
        "paths": [{"path": p, "requests": n}
                  for p, n in _rows(a.get("paths"), "clientRequestPath")],
    }
