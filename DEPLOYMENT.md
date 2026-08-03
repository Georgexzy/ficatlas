# Deploying FicAtlas publicly

Goes from the Tailscale-only development stack to a site on the internet,
without opening a port on the router or publishing the home IP address.

Everything below is free. Cloudflare Tunnel is available on the free plan and
needs no card.

---

## How it fits together

```
   visitor ──HTTPS──> Cloudflare edge ──existing outbound tunnel──> cloudflared
                                                                        │
                                                          compose network (no host ports)
                                                                        │
                                                      frontend:3000 ──> backend:8000 ──> db:5432
```

The important property: **cloudflared dials out**. Nothing connects inward, so
there is no port forward, the home IP never appears in DNS, and the router
configuration does not change. Turning the tunnel off makes the site vanish; it
cannot leave a hole behind.

Under the production overlay only the frontend publishes a port, and only on
`127.0.0.1` — so the box itself can still reach the app, and nothing else on the
LAN can bypass Cloudflare to get at it.

---

## One-time setup

### 1. Secrets

```bash
cp .env.example .env
openssl rand -base64 32        # paste into POSTGRES_PASSWORD
```

`.env` is gitignored. The development password (`ficatlas`) is in the git
history and must be treated as public.

**On an existing install** the database already has the old password baked in —
`POSTGRES_PASSWORD` is only read when Postgres initialises an empty data
directory. Rotate it explicitly:

```bash
docker compose exec db psql -U ficatlas -c "ALTER USER ficatlas PASSWORD 'the-new-one';"
```

then put that same value in `.env`.

### 2. Create the tunnel

At <https://one.dash.cloudflare.com> → **Networks → Tunnels → Create a tunnel**
→ **Cloudflared** → name it → choose **Docker**.

The dashboard shows a `docker run … --token eyJ…` command. Copy only the
`eyJ…` token into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`. It grants control of the
tunnel — treat it as a password.

Then under **Public Hostname**, add:

| field | value |
|---|---|
| Subdomain / domain | whatever you own, e.g. `ficatlas.example.com` |
| Service type | `HTTP` |
| URL | `frontend:3000` |

`frontend:3000` is the compose service name — cloudflared resolves it on the
compose network, which is why the frontend needs no published port.

### 3. Decide who may register

In `.env`:

- `SIGNUP_MODE=closed` — nobody can register
- `SIGNUP_MODE=invite` + `SIGNUP_CODE=…` — only people with the code (default)
- `SIGNUP_MODE=open` — anyone

**Create your own account before opening registration.** The first account ever
created becomes `owner` (see `init_db.py`), and there is no other way to get
that role.

### 4. Start it

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Add both `-f` flags every time, including for `logs`, `restart` and `down` —
omitting the overlay silently falls back to the development configuration, which
would republish Postgres and port 8000 on the host.

Worth defining once:

```bash
alias ficprod='docker compose -f ~/ficatlas/docker-compose.yml -f ~/ficatlas/docker-compose.prod.yml'
```

---

## What the overlay changes, and why

| | development | production |
|---|---|---|
| Postgres port | `127.0.0.1:5432` | not published |
| Backend port | `127.0.0.1:8000` | not published |
| Frontend port | `0.0.0.0:3000` | `127.0.0.1:3000` |
| Backend command | `--reload` | `--workers 2` |
| Code | bind-mounted from the host | baked into the image |
| DB password | `ficatlas` | from `.env`, no default |
| Session cookie | plain HTTP | `Secure` |
| Client IP | socket address | `CF-Connecting-IP` |
| Memory limits | none | per service |

Two of those are subtle:

**`COOKIE_SECURE`** cannot simply be hardcoded on. Over Tailscale the site is
plain HTTP, and a `Secure` cookie would never be sent back — login would appear
to succeed and every subsequent request would read as logged out.

**`TRUST_PROXY_HEADER`** must stay off without the tunnel. Cloudflare overwrites
`CF-Connecting-IP` on every request it proxies and strips any inbound copy, so
behind the tunnel it is trustworthy. Exposed directly it is trivially forged,
and believing it would let one client present a new identity per request and
bypass the rate limiter completely.

---

## Recommended Cloudflare settings

The tunnel alone gets the site online. These make it stand up to the internet:

- **SSL/TLS → Overview → Full (strict)**
- **Security → Bots → Bot Fight Mode** — on. Fanfiction indexes attract scrapers.
- **Security → WAF → Rate limiting rules** — free plan allows one. Suggested:
  `/api/*` at 60 requests/minute per IP. The in-process limiter
  (`backend/ratelimit.py`) is the backstop; Cloudflare's runs at the edge, so
  floods never reach the house at all.
- **Caching → Cache Rules** — bypass cache for `/api/*`. Search results are
  per-query and session-dependent; caching them serves one visitor's results to
  another.
- **Speed → Brotli** — on.

Do **not** enable "Always Use HTTPS" redirects *and* leave `COOKIE_SECURE=false`.
Either both or neither.

---

## Checks before announcing it anywhere

```bash
# Nothing but the frontend, and only on loopback.
docker compose -f docker-compose.yml -f docker-compose.prod.yml config \
  | grep -A3 ports

# Anonymous visitors get no admin surface. All of these must be 401.
for p in import-url discover-ao3 dedup-crossposts crawl-reset-breaker; do
  curl -s -o /dev/null -w "$p %{http_code}\n" -X POST https://YOUR-DOMAIN/api/library/$p
done

# Rate limiter is live: the 11th of these must be 429.
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code} " -X POST https://YOUR-DOMAIN/api/auth/login \
    -d "username=probe$i&password=xxxxxx"
done; echo
```

There is also a logged-out UI sweep in the scratchpad harness
(`deploy-audit.js`) that walks every page with no session and reports console
errors, failed requests and admin-only wording still visible to strangers. That
is the check that caught import buttons rendering for anonymous visitors and
`/story/1` returning a 500 rather than a 404.

---

## Operating notes

**Backups.** The index is ~19.7M rows and took weeks to build. The Postgres
volume is the only copy.

```bash
docker compose exec -T db pg_dump -U ficatlas -Fc ficatlas > ficatlas-$(date +%F).dump
```

**Watch after opening up:** the 429 rate in the backend logs (the limiter
working, or set too tight), Postgres connection count, and container memory
against the limits in the overlay — the worker is the one most likely to grow on
a bad batch, and capping it means it gets restarted rather than taking the
database down with it.

**Reader traffic is the expensive part.** Search is bounded, but serving chapter
bodies is not. If it becomes a problem, the reader is the piece to gate behind
an account, not search.
