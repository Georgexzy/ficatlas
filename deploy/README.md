# Deploying FicAtlas

Two sites, one database.

| | dev | public |
|---|---|---|
| reached at | `https://guserver.tail800dcb.ts.net` (tailnet) | `https://ficatlas.com` |
| compose project | `ficatlas` | `ficatlas-public` |
| code | bind-mounted, live reload | baked into a SHA-tagged image |
| reading | open | needs an account |
| signup | open | invite code |
| worker | **yes** — owns all harvesting | no |
| database | ← the same Postgres → | |

## Why the database is shared

It is 36GB and the disk has ~50GB free, so a second copy does not fit. This is a
constraint, not a preference, and it has one real consequence: **a schema change
is live for the public site the moment dev applies it.** Keep DDL additive.
`init_db.py` already is — idempotent `CREATE ... IF NOT EXISTS` — so the normal
case is safe. A destructive migration needs planning, because both colours and
both projects will be running against it at once.

What gets promoted here is *code*. There is one corpus and it is the product;
dev testing against the real index is a feature, not a compromise.

## Deploying

```bash
deploy/promote.sh --dry-run    # what would happen
deploy/promote.sh              # build, verify, switch
deploy/promote.sh --status     # what is live
deploy/promote.sh --rollback   # back to the previous colour
```

The public site is served through nginx, which points at one of two colours.
`promote.sh` starts the colour that is *not* live, waits for it to genuinely
answer, and only then repoints nginx and reloads. A reload lets in-flight
requests finish on the old workers, so nobody sees a dropped connection and
nobody sees a half-started app — the new version proves it serves before a
single visitor reaches it.

The previous colour stays up for `GRACE_SECONDS` (default 120) afterwards. That
window is the entire reason rollback is instant rather than a rebuild.

**Rollback is only possible during that window.** After it, the old colour is
stopped and rolling back means deploying an older SHA.

## Disk

The database volume is ~41GB on a disk that runs above 80% full, so deploy
artefacts have to be kept in check. `promote.sh` prunes build cache and keeps
the last two tags of each image — do not remove that step, because a full disk
stops Postgres writing and the site then fails for a reason with nothing to do
with the deploy that caused it.

The backend image was 11.8GB and is now 533MB. There was no `.dockerignore`, so
`COPY . .` baked `backend/data/` — a 6.9GB AO3 metadata dump and a 1.1GB
HuggingFace cache — into every image, an 8.53GB layer against 271MB of actual
dependencies. It was paid three times: image size, an 8GB build context shipped
on every build, and a fresh copy per retained deploy generation. Both
`.dockerignore` files are load-bearing; deleting them silently restores all of
that.

Container logs are capped at 10MB x 3 per service in both compose files. Docker's
json-file driver is unbounded by default, and nginx logs every request.

## The tunnel

`cloudflared` dials **out** to Cloudflare; nothing dials in. The home IP is never
in DNS and the router needs no port forward.

Nothing in the public project publishes a host port. That is deliberate: a
published port would be a second way in that bypasses Cloudflare entirely — no
WAF, no rate limiting, and no Access on `/admin`. It is also what makes
`TRUST_PROXY_HEADER=true` safe, since `CF-Connecting-IP` can then only have come
from Cloudflare, which overwrites it on every proxied request and strips any
inbound copy. **If you ever publish a port here, set that back to `false`** — the
header is trivially forged, and trusting it would let one client wear a new
identity per request and bypass rate limiting completely.

### Cloudflare dashboard settings

Public hostname (Networks → Tunnels → your tunnel → Public Hostname):

| field | value |
|---|---|
| subdomain | *(blank)* — and a second entry for `www` |
| domain | `ficatlas.com` |
| service | `HTTP` → `localhost:8080` |

`localhost` because cloudflared shares nginx's network namespace
(`network_mode: service:nginx`), so nginx *is* localhost to it. A service name
like `nginx:8080` puts Docker's embedded DNS in the path and is the step that
most often refuses to save. nginx listens on both IPv4 and IPv6 — `localhost`
resolves to `::1` first, and an IPv4-only listener refuses it while answering
`127.0.0.1` perfectly, which looks like a broken tunnel rather than a missing
listener.

It must point at **nginx**, not at a web container. Pointing it at `web-blue`
pins the public site to one colour and makes every deploy visible again.

Access policy for the admin surface (Zero Trust → Access → Applications):

| field | value |
|---|---|
| application | Self-hosted, `ficatlas.com/admin` |
| policy | Allow → Emails → *your address* |

Cloudflare evaluates the most specific path first, so this covers `/admin`
without touching the rest of the site. It is a second, independent gate: the app
already checks roles, and this means an app-level auth bug still does not expose
admin.

## Rate limiting and caching

The API rate-limits per `CF-Connecting-IP`, and nginx passes it through
untouched — losing that header would put every visitor in one bucket and the
limiter would throttle the whole site as though it were one abusive client.

Search responses already send `Cache-Control: public, max-age=120,
stale-while-revalidate=480` for anonymous visitors and `private, no-store` for
signed-in ones, so Cloudflare will cache the popular anonymous queries. That is
the shared cache the search code was written for, and on this box it matters more
than usual: a cold search reads from a database far larger than the page cache.

## When something is wrong

```bash
deploy/promote.sh --status
docker compose -p ficatlas-public -f docker-compose.public.yml logs --tail=50 nginx
docker compose -p ficatlas-public -f docker-compose.public.yml logs --tail=50 cloudflared
```

`nginx` healthy but the site down usually means the tunnel: check `cloudflared`
for `Registered tunnel connection`. The site down with the tunnel connected
usually means the active colour — check the health of `web-*` and `api-*`.
