"""
Per-IP request limiting, for when the site is reachable from the internet.
=========================================================================

Over Tailscale the only clients were trusted, so nothing here was needed. A
public deployment changes that: search is the expensive endpoint in this app
(whole-index queries over 19.7M rows) and signup is the one an open internet
actually attacks. The existing protections do not cover either — login has a
per-USERNAME lockout, which does nothing against a script that tries one
password against a thousand usernames, and search has no limit at all.

Deliberately in-process and approximate. A shared Redis counter would be exact
across replicas, but there is one API container, and the failure this guards
against is a crude flood rather than a determined distributed attacker — for
that, the Cloudflare side is the right layer (see DEPLOYMENT.md). Being wrong at
the margin costs nothing; adding a second service to the stack does.

Buckets are (ip, class) with a fixed window. Fixed windows can let through up to
2x the limit across a boundary, which is fine for the numbers used here and
avoids keeping a timestamp list per client.
"""

import hmac
import os
import threading
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse

ENABLED = os.getenv("RATE_LIMIT", "true").lower() in ("1", "true", "yes")

# Requests per window per IP, by path class. Search is the expensive one and
# also the one a real person hits repeatedly while refining a query, hence a
# limit that is generous per minute but still far below what a script does.
WINDOW = int(os.getenv("RATE_WINDOW_SEC", "60"))

# Counters live in this process, so with N uvicorn workers there are N of them
# and a client is spread across all N by the accept queue. Left alone, running
# four workers would silently quadruple every limit — the numbers below would
# stop meaning what they say the moment concurrency was raised for performance.
#
# Dividing by the worker count keeps the aggregate roughly at the configured
# figure. Roughly is the right word and always was: this is a fixed-window
# counter that already admits up to 2x across a boundary, and the distribution
# across workers is only approximately even. It guards against a crude flood,
# and for that being wrong at the margin costs nothing. A shared Redis counter
# would be exact and is not worth a second service — see the module docstring.
#
# Floored at 1 so a large worker count cannot round a limit down to zero and
# lock everybody out.
WORKERS = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))


def _per_worker(total: int) -> int:
    return max(1, total // WORKERS)


LIMITS = {
    "auth":   _per_worker(int(os.getenv("RATE_AUTH", "10"))),    # brute force
    "search": _per_worker(int(os.getenv("RATE_SEARCH", "90"))),  # expensive
    "read":   _per_worker(int(os.getenv("RATE_READ", "300"))),   # everything else
}

# Trusting a client-supplied header for the caller's identity is only safe when
# something upstream is guaranteed to overwrite it. Cloudflare sets
# CF-Connecting-IP on every request it proxies and strips any inbound copy, so
# it is trustworthy behind the tunnel and forgeable without it — which is why
# this is off unless the deployment says otherwise.
TRUST_PROXY = os.getenv("TRUST_PROXY_HEADER", "false").lower() in ("1", "true", "yes")


# Server-side rendering calls this API from inside the compose network, and so
# does every browser request to /api/* — the frontend rewrites those to the
# backend. Both therefore arrive from the frontend container's address, which
# makes the source address useless as a discriminator:
#
#   * count them as one client and the entire site shares ONE bucket. Story
#     pages, fandom hubs, /fandoms and the sitemap all render server-side, so
#     the deployment caps at RATE_READ renders a minute regardless of hardware.
#     Under load that looks like hub pages failing while direct API calls are
#     fine — which is how it was found: 839 errors, zero successes, in a load
#     run against /fandom/.
#   * exempt them by private source address and the limiter stops applying to
#     proxied browser traffic too, which is all of it. That is worse.
#
# So the discriminator is a shared secret the render code sends and a browser
# cannot: INTERNAL_RENDER_TOKEN, held only in server environments. Proxied
# browser requests carry no such header and stay limited per client.
#
# A header alone would be forgeable — the proxy passes client headers through —
# so the value has to be a real secret. Unset means no exemption: the safe
# direction is over-limiting our own rendering, not under-limiting the public.
INTERNAL_RENDER_TOKEN = os.getenv("INTERNAL_RENDER_TOKEN", "").strip()


def is_internal_render(request: Request) -> bool:
    """True for our own server-side rendering, which is not a client."""
    if not INTERNAL_RENDER_TOKEN:
        return False
    sent = request.headers.get("x-internal-render", "")
    # Constant-time: this is a secret comparison on a public path.
    return hmac.compare_digest(sent, INTERNAL_RENDER_TOKEN)


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Left-most entry is the original client; the rest are proxy hops.
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Exact paths, not prefixes: "/api/auth/signup" as a prefix also swallows
# /api/auth/signup-policy, a harmless GET the login page needs to render. Ten
# failed logins would then leave the login form unable to load at all.
AUTH_PATHS = {"/api/auth/login", "/api/auth/signup",
              # /forgot can be used to spam someone's inbox; /reset is a
              # code-guessing target. Both belong in the tight bucket.
              "/api/auth/forgot", "/api/auth/reset"}
# Submitting a takedown hides a story's text immediately and without review, so
# it is the one public endpoint whose abuse has an editorial effect rather than
# just a cost. Shares the tight "auth" budget deliberately — a real author files
# one request, not ten a minute.
TAKEDOWN_PATH = "/api/takedown"
# Verification makes an outbound request to AO3 or FanFiction.net on our behalf,
# so an unbounded caller here would have us hammering someone else's server. It
# shares the tight bucket for that reason rather than because it is risky in
# itself. /revoke is NOT listed: withdrawing consent must never be the throttled
# path, for the same reason it needs no proof.
PERMISSION_PATHS = {"/api/permissions/challenge", "/api/permissions/verify",
                    # /restrict needs no proof, which makes it the one path here
                    # that could be used to de-index other people's work at scale.
                    # Same mitigation the takedown form uses: throttled, stamped
                    # with a source address, and always reversible.
                    "/api/permissions/restrict"}


def path_class(path: str) -> str:
    p = path.rstrip("/")
    if p in AUTH_PATHS or p == TAKEDOWN_PATH or p in PERMISSION_PATHS:
        return "auth"
    if path.startswith("/api/search"):
        return "search"
    return "read"


class _Counter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], int] = defaultdict(int)
        self._window = 0

    def hit(self, ip: str, cls: str) -> tuple[bool, int]:
        """Record a request. Returns (allowed, seconds_until_reset)."""
        now = time.time()
        window = int(now // WINDOW)
        limit = LIMITS[cls]
        with self._lock:
            # One dict per window, dropped wholesale when the window turns —
            # so there is no unbounded growth and no sweeper thread.
            if window != self._window:
                self._window = window
                self._hits.clear()
            self._hits[(ip, cls)] += 1
            count = self._hits[(ip, cls)]
        reset = int((window + 1) * WINDOW - now)
        return count <= limit, max(reset, 1)


COUNTER = _Counter()


async def rate_limit_middleware(request: Request, call_next):
    if not ENABLED or not request.url.path.startswith("/api/"):
        return await call_next(request)

    # Our own rendering is not a client. See INTERNAL_RENDER_TOKEN above.
    if is_internal_render(request):
        return await call_next(request)

    cls = path_class(request.url.path)
    allowed, reset = COUNTER.hit(client_ip(request), cls)
    if not allowed:
        return JSONResponse(
            {"detail": "Too many requests. Please slow down."},
            status_code=429,
            headers={"Retry-After": str(reset)},
        )
    return await call_next(request)
