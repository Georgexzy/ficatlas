"use client"

import Link from "next/link"
import TakedownQueue from "./TakedownQueue"
import TrafficPanel from "./TrafficPanel"
import BackLink from "../BackLink"
import { useCallback, useEffect, useState } from "react"
import SiteHeader from "../SiteHeader"
import { useAuth } from "@/lib/auth"

// Index health, for whoever runs the box.
//
// All of this was previously only knowable from `docker compose logs` and psql.
// That is fine while the only user is the person who built it and is already in
// a terminal; it stops being fine when the question becomes "is this healthy"
// rather than "what did I just break".
//
// Everything on the page answers a question an operator actually has:
//
//   what is thin?          coverage, per site, per field
//   what happens next?     the crawler's upcoming targets and its cycle time
//   why is it slow?        the shared rate-limit state, with the current backoff
//
// Coverage is EXACT for small sites and SAMPLED for large ones, and each row
// says which. That split is not fussiness — the first version sampled the whole
// table and reported FictionAlley from whatever rows happened to fall in the
// sample, which was 61 to 115 of them, giving 21%, 23% and 34% on three
// consecutive runs for a field whose real value is 18.5%. It presented that with
// exactly the same confidence as AO3's 33,000-row estimate.
//
// The cause is that TABLESAMPLE draws whole BLOCKS, and rows are clustered on
// disk by import batch, so a small site lands in a handful of blocks. Sites
// under 400k rows are now counted properly instead — FictionAlley's exact
// figures come back in under a second.
interface Coverage {
  site: string; sampled: number; total: number
  /** True when every row was counted, false when this is a block sample. */
  exact: boolean
  /** Fields this site does not publish at all — not gaps, just not its vocabulary. */
  na: string[]
  no_words: number; no_kudos: number; no_chars: number
  no_ships: number; no_genres: number; no_summary: number; no_date: number
  /** AO3 rows with no fandom, which the tag-page harvest cannot see at all. */
  unreachable_by_listing?: number
}
interface Overview {
  tables: Record<string, number>
  coverage: Coverage[]
  coverage_sample: number
  crawl: {
    mode: string; pinned: string[]; rotate_count: number; pool: number
    cursor: number; upcoming: string[]; cycle_hours: number | null
    direct_crawl: boolean; disabled: { ao3: boolean; ffnet: boolean }
  }
  budget: { host: string; interval_s: number; queued_s: number }[]
  takedowns_pending: number
  // null when the count timed out under write load — shown as "—" rather than
  // as a confident zero, which would read as "nothing is hosted".
  hosted_public: number | null
  delisted: number | null
  cached?: boolean
  age_s?: number
}

const SITE_LABEL: Record<string, string> = {
  ao3: "AO3", ffnet: "FanFiction.net", fictionalley: "FictionAlley",
}

// The fields worth reporting, and what a gap in each actually costs. Ordered by
// how much the absence hurts: word_count and kudos drive ranking, so a row
// missing them cannot be sorted sensibly at all.
const FIELDS: { key: keyof Coverage; label: string; why: string }[] = [
  { key: "no_words",   label: "Length",   why: "Sorting by length and the word-count filter both skip these" },
  { key: "no_kudos",   label: "Kudos",    why: "Nothing to rank identically-titled works by" },
  { key: "no_chars",   label: "Characters", why: "Character filters cannot match them" },
  { key: "no_ships",   label: "Ships",    why: "Ship filters cannot match them" },
  { key: "no_genres",  label: "Genres",   why: "FF.net's only content signal" },
  { key: "no_summary", label: "Summary",  why: "Cards show a title and nothing else" },
  { key: "no_date",    label: "Date",     why: "Excluded from date filters and 'recently updated'" },
]

function pct(missing: number, total: number): number {
  return total > 0 ? Math.round((missing / total) * 100) : 0
}

/** Coverage — the share that HAS the field. The bars show this, not the gap. */
function have(missing: number, sampled: number): number {
  return sampled > 0 ? Math.round(((sampled - missing) / sampled) * 100) : 0
}

/** 12,700,000 -> "12.7M". Percentages of a sample do not give anyone a sense of
 *  scale on a 20M-row index; "1.2M of 13.5M works" does. */
function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
  if (n >= 1_000)     return `${Math.round(n / 1_000)}k`
  return n.toLocaleString()
}

// WHY a field is thin, where the reason is known and fixed rather than a bug to
// chase. This is the piece the page was missing: the two biggest bars on it are
// AO3 kudos and AO3 summary, both at ~94%, and with no explanation they read as
// "the index is broken" when they are a known property of where those rows came
// from. Keyed `site:field`; anything absent just shows no note.
const CAUSE: Record<string, string> = {
  "ao3:no_summary":
    "12.9M AO3 rows came from the bulk metadata dump, which has no summary field at all. Freshly crawled works do get one, so this closes only by re-crawling.",
  "ao3:no_kudos":
    "The same bulk dump carries no engagement figures. Works the crawler has visited since do have them.",
  // FF.net's note is deliberately NOT "it has favourites, not kudos", which is
  // the obvious sentence and is wrong: 359,223 FF.net rows carry a kudos figure
  // and exactly 0 carry a favourites one, because ffnet_wayback.py parses
  // "Favs:" straight INTO the kudos column — that is the column the whole app
  // ranks on, and there is a test pinning it. So the bar is real, and what it
  // is actually measuring is how far enrichment has got.
  "ffnet:no_kudos":
    "FanFiction.net has no kudos of its own; its favourites are stored in this column as the nearest equivalent. The 6.6M-row bulk dump carries no engagement figures at all, so only the works the crawler has since enriched have one.",
}

export default function AdminPage() {
  const [tab, setTab] = useState<"health" | "takedowns" | "traffic">("health")
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab")
    if (t === "takedowns" || t === "traffic") setTab(t)
  }, [])
  const { user, loading: authLoading } = useAuth()
  const isAdmin = !!user?.can_manage
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async (fresh = false) => {
    setError(null)
    try {
      const r = await fetch(`/api/admin/overview${fresh ? "?refresh=true" : ""}`,
                            { credentials: "include" })
      if (!r.ok) throw new Error(`Could not load (${r.status}).`)
      setData(await r.json())
    } catch (e: any) { setError(e.message) }
  }, [])

  useEffect(() => { if (isAdmin) load() }, [isAdmin, load])

  const runJob = async (job: string) => {
    setBusy(job); setNote(null)
    try {
      const fd = new FormData(); fd.append("limit", "300")
      const r = await fetch(`/api/admin/run/${job}`, { method: "POST", body: fd, credentials: "include" })
      const d = await r.json().catch(() => null)
      setNote(r.ok
        ? "Started. It runs in the background — reload in a few minutes to see the coverage move."
        : (d?.detail || `Could not start (${r.status}).`))
    } catch { setNote("Could not start that pass.") }
    finally { setBusy(null) }
  }

  if (authLoading) return (
    <div className="settings-shell"><SiteHeader />
      <BackLink fallback="/settings" fallbackLabel="Settings" /><p className="loading">Loading…</p></div>
  )
  if (!isAdmin) return (
    <div className="page-prose">
      <SiteHeader />
      <h1>Not found</h1>
      <p>There is nothing here for this account.</p>
      <p><Link href="/" className="card-btn card-btn--primary">Back to search</Link></p>
    </div>
  )

  return (
    <div className="settings-shell">
      <SiteHeader />
      <BackLink fallback="/settings" fallbackLabel="Settings" />

      {/* Two owner-only surfaces, one page.
          Index health and the takedown queue were separate routes reached only
          from Settings, each re-implementing the same shell, the same back link
          and — more importantly — the same "not an operator" gate. Two copies of
          an access check is one too many: they can drift, and the one that
          drifts laxer is the bug. /takedowns is kept as a redirect. */}
      <div className="admin-tabs">
        <button className={`library-tab ${tab === "health" ? "library-tab--on" : ""}`}
          onClick={() => setTab("health")}>Index health</button>
        <button className={`library-tab ${tab === "takedowns" ? "library-tab--on" : ""}`}
          onClick={() => setTab("takedowns")}>Takedown requests</button>
        <button className={`library-tab ${tab === "traffic" ? "library-tab--on" : ""}`}
          onClick={() => setTab("traffic")}>Traffic</button>
      </div>

      {tab === "traffic" ? <TrafficPanel /> :
       tab === "takedowns" ? <TakedownQueue /> : (
      <>
      <h1 className="settings-title">Index health</h1>

      {error && <p className="settings-save-error" role="alert">{error}</p>}
      {!data && !error && <p className="loading">Reading the index…</p>}

      {data && (
        <>
          <div className="admin-tiles">
            <Tile label="Works indexed" value={data.tables.stories} approx />
            <Tile label="Readable here" value={data.hosted_public} />
            <Tile label="Delisted" value={data.delisted} />
            <Tile label="Takedowns waiting" value={data.takedowns_pending}
                  onClick={data.takedowns_pending > 0 ? () => setTab("takedowns") : undefined} />
          </div>

          {data.cached && (data.age_s ?? 0) > 0 && (
            <p className="admin-note">
              Figures are {data.age_s}s old. They are cached for three minutes —
              the underlying queries scan a 19.7M-row table and are slowest
              exactly when the box is busy, which is when you would be reading
              this. <button className="linklike" onClick={() => load(true)}>Recalculate now</button>.
            </p>
          )}

          <section className="settings-group">
            <h2 className="settings-group__title">Field coverage</h2>
            <p className="settings-group__hint">
              How much of each archive actually carries each field.{" "}
              <strong>A longer bar is better</strong> — it is the share of works
              that HAVE the field, not the share missing it. Sites under 400,000
              works are counted exactly; larger ones are estimated from a sample
              of {data.coverage_sample.toLocaleString()}, which at that size is
              accurate to well under a percent.
              {" "}A short bar is not automatically a bug: some of these fields do
              not exist at the source, and the ones with a known cause say so
              underneath. It becomes a bug when a filter looks like it works and
              matches almost nothing.
            </p>
            {data.coverage.map(c => (
              <div key={c.site} className="admin-site">
                <h3 className="admin-site__name">
                  {SITE_LABEL[c.site] ?? c.site}
                  <span className="admin-site__n">
                    {c.total.toLocaleString()} works ·{" "}
                    {c.exact
                      ? "every row counted"
                      : `estimated from ${c.sampled.toLocaleString()}`}
                  </span>
                </h3>
                <div className="admin-bars">
                  {FIELDS.map(f => {
                    // A field the site does not have is not a gap to fix. AO3
                    // has literally zero rows with a genre, because genres are
                    // FF.net's vocabulary — showing that as a full red bar said
                    // "fix this" about something that cannot be fixed, and
                    // devalued the bars that do mean something.
                    const na = c.na.includes(f.key as string)
                    const p = pct(c[f.key] as number, c.sampled)
                    if (na) return (
                      <div key={f.key} className="admin-bar-row">
                        <div className="admin-bar admin-bar--na">
                          <span className="admin-bar__label">{f.label}</span>
                          <span className="admin-bar__track" />
                          <span className="admin-bar__pct">n/a</span>
                        </div>
                        {/* Spelled out rather than left as a dash. "n/a" next to
                            a row of percentages invites the reading "we failed
                            to measure it"; the truth is there is nothing to
                            measure, and that is not a gap anyone should chase. */}
                        <p className="admin-bar__detail">
                          {SITE_LABEL[c.site] ?? c.site} does not publish this field
                        </p>
                      </div>
                    )
                    // The bar shows COVERAGE, not the gap. It used to fill to
                    // the missing share, so AO3 kudos and summary painted two
                    // nearly-full red bars for a page titled "Index health" —
                    // the exact opposite of how a filled bar reads. Now a full
                    // green bar means the field is there.
                    const h = have(c[f.key] as number, c.sampled)
                    const cause = CAUSE[`${c.site}:${String(f.key)}`]
                    const hasN = Math.round(c.total * (h / 100))
                    return (
                      <div key={f.key} className="admin-bar-row">
                        <div className="admin-bar" title={f.why}>
                          <span className="admin-bar__label">{f.label}</span>
                          <span className="admin-bar__track">
                            <span className={`admin-bar__fill ${h <= 10 ? "is-bad" : h <= 50 ? "is-mid" : "is-ok"}`}
                                  style={{ width: `${h}%` }} />
                          </span>
                          {/* Percentage AND count. A share of a 33,485-row
                              sample means nothing next to a heading that says
                              13,506,241 works; "1.2M of 13.5M" is the number a
                              person can act on. */}
                          <span className="admin-bar__pct">{h}%</span>
                        </div>
                        <p className="admin-bar__detail">
                          {compact(hasN)} of {compact(c.total)} works have this
                          {cause && <> · <span className="admin-bar__cause">{cause}</span></>}
                        </p>
                      </div>
                    )
                  })}
                </div>
                {c.unreachable_by_listing != null && c.unreachable_by_listing > 0 && (
                  <p className="admin-site__note">
                    Most of this closes on its own: the listing harvest gets these
                    same fields <strong>twenty works per request</strong> by walking
                    fandom tag pages, and it is running now.{" "}
                    <strong>{c.unreachable_by_listing.toLocaleString()}</strong> of
                    these rows have no fandom at all, so a tag-page walk cannot see
                    them by construction — those are the ones the stub pass below
                    fetches one at a time, worst gaps first. Filling one also gives
                    it a fandom, after which the cheap route can maintain it.
                  </p>
                )}
              </div>
            ))}
          </section>

          <section className="settings-group">
            <h2 className="settings-group__title">What the crawler does next</h2>
            <dl className="admin-facts">
              <dt>Mode</dt>
              <dd>
                <strong>{data.crawl.mode}</strong>
                {data.crawl.mode === "pinned" && (
                  <span className="admin-warn">
                    {" "}— only your own fandoms gain new works. Everything else is
                    frozen at whatever the bulk import left.
                  </span>
                )}
              </dd>
              <dt>Up next</dt>
              <dd>{data.crawl.upcoming.length
                ? data.crawl.upcoming.map(f => <span key={f} className="admin-chip">{f}</span>)
                : <em>nothing configured</em>}</dd>
              <dt>Rotation</dt>
              <dd>
                {data.crawl.rotate_count} of {data.crawl.pool} fandoms per pass
                {data.crawl.cycle_hours != null && (
                  <> · a full sweep takes about <strong>{data.crawl.cycle_hours}h</strong></>
                )}
              </dd>
              <dt>Direct crawling</dt>
              <dd>
                {data.crawl.direct_crawl ? "on" : "off"}
                {data.crawl.disabled.ao3 && <span className="admin-warn"> · AO3 auto-disabled</span>}
                {data.crawl.disabled.ffnet && <span className="admin-warn"> · FF.net auto-disabled</span>}
              </dd>
            </dl>
            <p className="settings-group__hint">
              Change any of this in <Link href="/settings">Settings</Link>.
            </p>
          </section>

          {data.budget.length > 0 && (
            <section className="settings-group">
              <h2 className="settings-group__title">Rate limits</h2>
              <p className="settings-group__hint">
                Shared across every process, so a maintenance script and the
                background worker queue behind each other rather than each
                politely limiting itself while the site sees double.
              </p>
              {data.budget.map(b => (
                <div key={b.host} className="setting-row">
                  <div className="setting-row__label">
                    <span className="setting-row__name">{b.host}</span>
                    <span className="setting-row__hint">
                      {b.interval_s > 10
                        ? `Backed off to one request every ${b.interval_s}s — the site has been refusing or throttling us, so crawling is slow on purpose.`
                        : `One request every ${b.interval_s}s.`}
                    </span>
                  </div>
                  <span className={`badge ${b.interval_s > 10 ? "badge--delisted" : ""}`}>
                    {b.queued_s > 0 ? `${b.queued_s}s queued` : "idle"}
                  </span>
                </div>
              ))}
            </section>
          )}

          <section className="settings-group">
            <h2 className="settings-group__title">Run a pass now</h2>
            <p className="settings-group__hint">
              Each of these is already on a timer. These buttons are for when you
              have just fixed whatever was blocking one and do not want to wait
              half an hour to find out whether it worked.
            </p>
            {note && <p className="admin-note">{note}</p>}
            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Fill AO3 rows with no fandom</span>
                <span className="setting-row__hint">
                  Only the rows the listing harvest cannot reach, worst gaps first.
                  One request each, so this is deliberately the small half of the
                  job — the cheap bulk route handles everything with a fandom.
                </span>
              </div>
              <button className="btn btn--ghost" disabled={busy === "ao3_stubs"}
                onClick={() => runJob("ao3_stubs")}>
                {busy === "ao3_stubs" ? "Starting…" : "Run"}
              </button>
            </div>
            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Backfill FF.net characters</span>
                <span className="setting-row__hint">
                  From Wayback captures — FF.net publishes characters and pairings,
                  but neither bulk dump carries them.
                </span>
              </div>
              <button className="btn btn--ghost" disabled={busy === "ffnet_meta"}
                onClick={() => runJob("ffnet_meta")}>
                {busy === "ffnet_meta" ? "Starting…" : "Run"}
              </button>
            </div>
          </section>

          <div className="settings-actions">
            <button className="btn btn--ghost" onClick={() => load(true)}>Refresh</button>
          </div>
        </>
      )}
      </>
      )}
    </div>
  )
}

function Tile({ label, value, approx, href, onClick }: {
  label: string; value: number | null; approx?: boolean; href?: string
  onClick?: () => void
}) {
  const body = (
    <>
      <span className="admin-tile__value">
        {value == null ? "—"
          : `${approx && value > 1000 ? "~" : ""}${value.toLocaleString()}`}
      </span>
      <span className="admin-tile__label">{label}</span>
    </>
  )
  // onClick before href: the takedown tile now switches to a tab on this very
  // page, so routing away to /takedowns and being redirected back would be a
  // round trip to arrive where we already are.
  if (onClick) {
    return <button type="button" onClick={onClick} className="admin-tile admin-tile--link">{body}</button>
  }
  return href
    ? <Link href={href} className="admin-tile admin-tile--link">{body}</Link>
    : <div className="admin-tile">{body}</div>
}
