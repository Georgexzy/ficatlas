"use client"

import Link from "next/link"
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
// Coverage is SAMPLED, and says so. Counting stubs exactly means a filtered scan
// of 13.1M rows at about ten seconds a column; a 200k-row sample is accurate to
// a fraction of a percent and returns immediately. An admin page that costs a
// minute of database time is one nobody opens twice.
interface Coverage {
  site: string; sampled: number
  no_words: number; no_kudos: number; no_chars: number
  no_ships: number; no_genres: number; no_summary: number; no_date: number
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

export default function AdminPage() {
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
    <div className="settings-shell"><SiteHeader /><p className="loading">Loading…</p></div>
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
                  href={data.takedowns_pending > 0 ? "/takedowns" : undefined} />
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
            <h2 className="settings-group__title">What is thin</h2>
            <p className="settings-group__hint">
              Share of rows with nothing recorded for each field, from a sample of{" "}
              {data.coverage_sample.toLocaleString()} — near-instant, and accurate
              to well under a percent. A gap is not a bug in itself: FF.net&apos;s bulk
              dump simply has no kudos column. It becomes one when a filter looks
              like it works and matches almost nothing.
            </p>
            {data.coverage.map(c => (
              <div key={c.site} className="admin-site">
                <h3 className="admin-site__name">
                  {SITE_LABEL[c.site] ?? c.site}
                  <span className="admin-site__n">{c.sampled.toLocaleString()} sampled</span>
                </h3>
                <div className="admin-bars">
                  {FIELDS.map(f => {
                    const p = pct(c[f.key] as number, c.sampled)
                    return (
                      <div key={f.key} className="admin-bar" title={`${p}% missing — ${f.why}`}>
                        <span className="admin-bar__label">{f.label}</span>
                        <span className="admin-bar__track">
                          <span className={`admin-bar__fill ${p >= 90 ? "is-bad" : p >= 50 ? "is-mid" : "is-ok"}`}
                                style={{ width: `${p}%` }} />
                        </span>
                        <span className="admin-bar__pct">{p}%</span>
                      </div>
                    )
                  })}
                </div>
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
                <span className="setting-row__name">Fill AO3 stub rows</span>
                <span className="setting-row__hint">
                  Rows indexed as a bare title. Fetches length, kudos and summary
                  from the work page, shortest titles first — those collide most.
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
    </div>
  )
}

function Tile({ label, value, approx, href }: {
  label: string; value: number | null; approx?: boolean; href?: string
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
  return href
    ? <Link href={href} className="admin-tile admin-tile--link">{body}</Link>
    : <div className="admin-tile">{body}</div>
}
