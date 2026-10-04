"use client"

import Link from "next/link"
import TakedownQueue from "./TakedownQueue"
import TrafficPanel from "./TrafficPanel"
import OutreachTab from "./OutreachTab"
import ToolsView, { DbActivity } from "./ToolsView"
import PassesView from "./PassesView"
import "./admin.css"
import { ADMIN_GROUPS, adminTabFor, type AdminTab } from "@/lib/adminTabs"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"
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
  // What each background job last managed to DO, and how stale that is. Every
  // figure is evidence the job left behind — a build timestamp, a watermark —
  // rather than a heartbeat it reported, because a heartbeat says "I ran" and
  // this says "I achieved something". See _job_evidence in api/admin.py.
  jobs?: {
    evidence: { key: string; label: string; why: string
                last_run: string | null; age_h: number | null
                stale_after_h: number; state: "ok" | "stale" | "unknown" }[]
    queues: { key: string; label: string; depth: number }[]
  }
  /** What is actually wrong, worst first — stale jobs and coverage gaps in one
   *  ranking, because a job three hours late matters less than six million
   *  works nobody can find by pairing and only one of those was visible.
   *  See _attention in api/admin.py. */
  attention?: { kind: "job" | "gap"; key: string; label: string
                detail: string; severity: number; works: number | null
                /** Decided server-side, next to the formula that produces the
                 *  numbers it cuts, and relative to the worst thing on the
                 *  list — the scale is unbounded by design. */
                band?: "high" | "mid" | "low" }[]
  storage?: { db_bytes: number
              objects: { name: string; bytes: number; rows: number }[] } | null
  // Sampled, not queried: grouping 20M rows by indexed_at measured 15.7s and
  // there is no index for it. per_day is null until an hour of samples exist.
  growth?: { per_day: number | null; window_h: number | null; samples: number }
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

/** 39225434112 -> "36.5 GB". Bytes are unreadable and this page is read, not parsed. */
function bytes(n: number): string {
  if (n >= 1e9) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1e6) return `${Math.round(n / 1024 ** 2)} MB`
  return `${Math.round(n / 1024)} kB`
}

/** "6.3h ago", "2.1 days ago". An ISO timestamp tells an operator nothing at a
 *  glance; the whole question about a background job is how long ago. */
function ago(hours: number | null): string {
  if (hours == null) return "never"
  // "0 min ago" is what rounding gives for anything under thirty seconds, and it
  // reads like a broken clock rather than like a job that has just run.
  if (hours * 60 < 1) return "just now"
  if (hours < 1) return `${Math.round(hours * 60)} min ago`
  if (hours < 48) return `${hours.toFixed(1)}h ago`
  return `${(hours / 24).toFixed(1)} days ago`
}

/** The same idea as ago() above, for a timestamp rather than an age in hours.
 *
 *  Separate rather than overloaded because the two are read differently: a job's
 *  age wants one decimal place ("13.4h ago" — is it late), and a person's wants
 *  the coarsest true answer ("today", "3 days ago"). Precision about when
 *  somebody last opened the site is not information, it is surveillance.
 *
 *  Naive UTC in, local out: `last_used` is a `timestamp without time zone`
 *  written from utcnow(), so it carries no offset and the Z has to be added or
 *  a browser west of UTC reads every timestamp as hours in the future.
 */
function agoStamp(iso: string | null): string {
  if (!iso) return "never"
  const t = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z").getTime()
  if (!Number.isFinite(t)) return "never"
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000))
  if (mins < 5) return "just now"
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return days === 1 ? "yesterday" : `${days} days ago`
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

// Who has an account, and — the column that actually matters — whether they
// could ever get back into it. See /api/admin/users.
interface Person {
  username: string; role: string; email: string | null
  created_at: string | null; last_login: string | null; last_seen: string | null
  recoverable: boolean
  sessions: number; follows: number; saved: number; imported: number
}

/** An open reset request. Never carries the code — see list_reset_requests. */
interface ResetRequest {
  username: string; email: string | null
  created_at: string; expires_at: string; emailed: boolean
}

// ── the control centre ──────────────────────────────────────────────────────
//
// This page was one 720px column, 7,022px tall on a desktop and 12,462px on the
// Audience tab — a report to scroll, not a place to run a site from. The width
// is now the screen's (capped at 1440px), the views sit on a rail grouped by the
// job they are for, and the first thing on screen is a strip of six answers:
// is the public site up, are the jobs alive, is the database busy, is the index
// growing, is anything waiting on moderation, can every account recover.
//
// Everything the old page said is still here — the explanations moved behind
// an "i" on each view rather than into the flow, because they are read once and
// then cost a screen every visit after.

function Explain({ children }: { children: React.ReactNode }) {
  return (
    <details className="cc-explain">
      <summary aria-label="What this shows">i</summary>
      <div className="cc-explain__body">{children}</div>
    </details>
  )
}

function ViewHead({ title, sub, children, explain }: {
  title: string; sub?: React.ReactNode; children?: React.ReactNode; explain?: React.ReactNode
}) {
  return (
    <header className="cc-viewhead">
      <div>
        <h1>{title}{explain && <Explain>{explain}</Explain>}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {children && <div className="cc-viewhead__actions">{children}</div>}
    </header>
  )
}

type Tone = "ok" | "mid" | "bad" | "none"

function Stat({ label, value, sub, tone = "none", onClick }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone; onClick?: () => void
}) {
  const body = (
    <>
      <span className="cc-stat__label"><span className={`cc-dot cc-dot--${tone}`} />{label}</span>
      <span className="cc-stat__value">{value}</span>
      {sub && <span className="cc-stat__sub">{sub}</span>}
    </>
  )
  return onClick
    ? <button type="button" className={`cc-stat cc-stat--${tone} cc-stat--link`} onClick={onClick}>{body}</button>
    : <div className={`cc-stat cc-stat--${tone}`}>{body}</div>
}

/** The public site, as a reader's browser sees it. */
function useSitePulse() {
  const [p, setP] = useState<{ ms: number; ok: boolean } | null>(null)
  useEffect(() => {
    let live = true
    const t0 = performance.now()
    fetchWithTimeout("/api/stats/totals", { cache: "no-store" }, 15000)
      .then(r => { if (live) setP({ ms: performance.now() - t0, ok: r.ok }) })
      .catch(() => { if (live) setP({ ms: performance.now() - t0, ok: false }) })
    return () => { live = false }
  }, [])
  return p
}

function useActivitySummary() {
  const [a, setA] = useState<{ total: number; max: number; locks: number; longest: number } | null>(null)
  useEffect(() => {
    fetchWithTimeout("/api/admin/activity", { credentials: "include" }, USER_TIMEOUT_MS)
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setA({ total: d.connections.total, max: d.connections.max,
                             locks: d.waiting_on_locks, longest: d.longest_s }))
      .catch(() => {})
  }, [])
  return a
}

export default function AdminPage() {
  const [tab, setTabState] = useState<AdminTab>("overview")
  // The Audience tab's zero-result queries, handed to the Outreach tab. Held
  // here because it crosses a tab boundary — see TrafficPanel.onGoToOutreach.
  const [outreachPrefill, setOutreachPrefill] = useState<string | undefined>(undefined)
  const [activePasses, setActivePasses] = useState(0)
  useEffect(() => {
    const to = adminTabFor(new URLSearchParams(window.location.search).get("tab"))
    if (to) setTabState(to)
  }, [])
  // The view is in the address, so a reload or a shared bookmark lands on it.
  // replaceState, not push: switching views is not navigation the back button
  // should have to walk through.
  const setTab = (t: AdminTab) => {
    setTabState(t)
    try {
      const u = new URL(window.location.href)
      u.searchParams.set("tab", t)
      window.history.replaceState(null, "", u.toString())
    } catch {}
    try { window.scrollTo({ top: 0 }) } catch {}
  }

  const { user, loading: authLoading } = useAuth()
  const isAdmin = !!user?.can_manage
  const [data, setData] = useState<Overview | null>(null)
  const [people, setPeople] = useState<Person[] | null>(null)
  const [resets, setResets] = useState<ResetRequest[] | null>(null)
  const [issued, setIssued] = useState<{ username: string; code: string } | null>(null)
  const [issuing, setIssuing] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [loadingFresh, setLoadingFresh] = useState(false)

  const load = useCallback(async (fresh = false) => {
    setError(null)
    if (fresh) setLoadingFresh(true)
    try {
      fetchWithTimeout("/api/auth/reset-requests", { credentials: "include" })
        .then(r => (r.ok ? r.json() : []))
        .then(setResets)
        .catch(() => setResets([]))
      fetchWithTimeout("/api/admin/users", { credentials: "include" })
        .then(r => (r.ok ? r.json() : null))
        .then(d => setPeople(d?.users ?? null))
        .catch(() => {})
      const r = await fetchWithTimeout(`/api/admin/overview${fresh ? "?refresh=true" : ""}`,
                            { credentials: "include" }, USER_TIMEOUT_MS)
      if (!r.ok) throw new Error(`Could not load (${r.status}).`)
      setData(await r.json())
    } catch (e: any) { setError(e.message) }
    finally { setLoadingFresh(false) }
  }, [])

  useEffect(() => { if (isAdmin) load() }, [isAdmin, load])
  const pulse = useSitePulse()
  const act = useActivitySummary()

  if (authLoading) return (
    <main id="main" className="settings-shell"><SiteHeader />
      <BackLink fallback="/settings" fallbackLabel="Settings" /><p className="loading">Loading…</p></main>
  )
  // TWO DIFFERENT FAILURES, told apart: signed out, and signed in without the
  // owner role. "Not found" for both once cost a real diagnosis — a stale bundle
  // looked exactly like a demotion.
  if (!user) return (
    <main id="main" className="page-prose">
      <SiteHeader />
      <h1>Sign in to continue</h1>
      <p>
        This page is for whoever runs the site, and you are not signed in on
        this browser.
      </p>
      <p>
        If you believe you <em>are</em> signed in, this browser is likely
        holding an old copy of the app — reload with{" "}
        <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd> (or{" "}
        <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd>) and try again.
      </p>
      <p>
        <Link href="/login?next=/admin" className="card-btn card-btn--primary">Sign in</Link>{" "}
        <Link href="/" className="card-btn">Back to search</Link>
      </p>
    </main>
  )
  if (!isAdmin) return (
    <main id="main" className="page-prose">
      <SiteHeader />
      <h1>Not for this account</h1>
      <p>
        You are signed in as <strong>{user.username}</strong>, which can search,
        read and keep a library — this page needs the owner role.
      </p>
      <p><Link href="/" className="card-btn card-btn--primary">Back to search</Link></p>
    </main>
  )

  const jobs = data?.jobs?.evidence ?? []
  const stale = jobs.filter(j => j.state !== "ok")
  const attention = data?.attention ?? []
  const noEmail = people?.filter(u => !u.recoverable).length ?? 0
  const badge: Partial<Record<AdminTab, { n: number; tone: Tone }>> = {
    overview: attention.length ? { n: attention.length, tone: attention.some(a => a.band === "high") ? "bad" : "mid" } : undefined,
    passes: activePasses ? { n: activePasses, tone: "mid" } : undefined,
    moderation: data?.takedowns_pending ? { n: data.takedowns_pending, tone: "bad" } : undefined,
    people: noEmail ? { n: noEmail, tone: "mid" } : undefined,
    crawler: data?.crawl && (data.crawl.disabled.ao3 || data.crawl.disabled.ffnet) ? { n: 1, tone: "bad" } : undefined,
  }

  return (
    <main id="main" className="settings-shell cc">
      <SiteHeader />
      <div className="cc-layout">
        <nav className="cc-rail" aria-label="Control centre">
          <div className="cc-rail__brand">
            <span className="cc-rail__eyebrow">FicAtlas</span>
            <span className="cc-rail__title">Control centre</span>
          </div>
          {ADMIN_GROUPS.map(g => (
            <div key={g.title} className="cc-rail__group">
              <span className="cc-rail__head">{g.title}</span>
              {g.tabs.map(t => (
                <button key={t.key} type="button"
                  className={`cc-rail__item ${tab === t.key ? "is-on" : ""}`}
                  aria-current={tab === t.key ? "page" : undefined}
                  onClick={() => setTab(t.key)}>
                  <span>{t.label}</span>
                  {badge[t.key] && <span className={`cc-pill cc-pill--${badge[t.key]!.tone}`}>{badge[t.key]!.n}</span>}
                </button>
              ))}
            </div>
          ))}
          <div className="cc-rail__foot">
            <Link href="/settings">Site settings</Link>
            <Link href="/" target="_blank">Open the site ↗</Link>
          </div>
        </nav>

        <div className="cc-main">
          {error && <p className="cc-msg cc-msg--bad" role="alert">{error}</p>}

          {tab === "audience" && <TrafficPanel
            onGoToOutreach={q => { setOutreachPrefill(q); setTab("outreach") }} />}
          {tab === "outreach" && <OutreachTab prefill={outreachPrefill} />}
          {tab === "moderation" && <TakedownQueue />}
          {tab === "tools" && (
            <>
              <ViewHead title="Tools" sub="Look things up and measure them, without a terminal. Nothing here writes."
                explain={<p>Each tool answers a question that used to need psql or curl: what the
                  database is running, what the index believes about one work, whether
                  search is answering in time, and whether the public pages respond
                  through the same proxy a reader uses.</p>} />
              <ToolsView />
            </>
          )}
          {tab === "passes" && (
            <>
              <ViewHead title="Passes" sub="Start a maintenance pass now instead of waiting for its timer. Preview first; it writes nothing." />
              <PassesView onCounts={setActivePasses} />
            </>
          )}

          {!data && !error && ["overview", "coverage", "crawler", "storage", "people"].includes(tab) &&
            <p className="cc-dim cc-loading">Reading the index…</p>}

          {data && tab === "overview" && (
            <>
              <ViewHead title="Overview"
                sub={data.cached && (data.age_s ?? 0) > 0
                  ? <>Index figures are {data.age_s}s old — cached for three minutes because they scan a 20M-row table.</>
                  : <>Index figures computed just now.</>}
                explain={<p>Every row under <em>Background jobs</em> is evidence a job left behind — a
                  build timestamp, a watermark, a queue draining — not a heartbeat it
                  reported. A heartbeat says “I ran”; evidence says “I achieved
                  something”, which is what catches a loop running happily over a
                  broken query. <code>popularity_rank</code> once sat frozen for
                  months with no row here at all.</p>}>
                <button className="cc-btn" disabled={loadingFresh} onClick={() => load(true)}>
                  {loadingFresh ? "Recalculating…" : "Recalculate"}
                </button>
              </ViewHead>

              <div className="cc-stats">
                <Stat label="Public site"
                  tone={!pulse ? "none" : !pulse.ok ? "bad" : pulse.ms > 2000 ? "mid" : "ok"}
                  value={!pulse ? "…" : pulse.ok ? "Up" : "Down"}
                  sub={pulse ? `API answered in ${Math.round(pulse.ms)}ms` : "checking"}
                  onClick={() => setTab("tools")} />
                <Stat label="Background jobs"
                  tone={!jobs.length ? "none" : stale.length ? (stale.length > 2 ? "bad" : "mid") : "ok"}
                  value={jobs.length ? `${jobs.length - stale.length}/${jobs.length}` : "—"}
                  sub={stale.length ? `${stale.length} stale: ${stale.slice(0, 2).map(s => s.label).join(", ")}${stale.length > 2 ? "…" : ""}` : "all showing recent evidence"} />
                <Stat label="Database"
                  tone={!act ? "none" : act.locks ? "bad" : act.total / act.max > 0.8 ? "mid" : "ok"}
                  value={data.storage ? bytes(data.storage.db_bytes) : "—"}
                  sub={act ? `${act.total}/${act.max} connections${act.locks ? ` · ${act.locks} waiting on a lock` : ""}${act.longest > 30 ? ` · longest ${act.longest}s` : ""}` : "…"}
                  onClick={() => setTab("tools")} />
                <Stat label="Works indexed" tone="none"
                  value={data.tables.stories != null ? compact(data.tables.stories) : "—"}
                  sub={data.growth?.per_day != null
                    ? `${data.growth.per_day >= 0 ? "+" : ""}${data.growth.per_day.toLocaleString()} a day`
                    : `measuring growth · ${data.growth?.samples ?? 0} samples`} />
                <Stat label="Takedowns waiting"
                  tone={data.takedowns_pending ? "bad" : "ok"}
                  value={data.takedowns_pending}
                  sub={`${data.delisted?.toLocaleString() ?? "—"} delisted · ${data.hosted_public?.toLocaleString() ?? "—"} readable here`}
                  onClick={() => setTab("moderation")} />
                <Stat label="Accounts"
                  tone={!people ? "none" : noEmail ? "mid" : "ok"}
                  value={people?.length ?? "—"}
                  sub={!people ? "…" : noEmail ? `${noEmail} cannot recover a password` : "all recoverable"}
                  onClick={() => setTab("people")} />
              </div>

              <div className="cc-cols cc-cols--overview">
                <section className="cc-card">
                  <header className="cc-card__head">
                    <h3>Needs attention <span className="cc-count">{attention.length}</span></h3>
                    <p>Ranked by what each costs a reader. The crawler works this list from the top.</p>
                  </header>
                  {attention.length === 0
                    ? <p className="cc-empty">Nothing. Every job has fresh evidence and no field gap is large enough to rank.</p>
                    : (
                      <ol className="cc-attn">
                        {attention.map(a => (
                          <li key={a.key} className={`cc-attn__item cc-attn__item--${a.band ?? "low"}`}>
                            <div className="cc-attn__row">
                              <span className="cc-attn__label">{a.label}</span>
                              <span className="cc-tag">{a.kind === "job" ? "job" : "coverage"}</span>
                            </div>
                            <p className="cc-attn__detail">{a.detail}</p>
                          </li>
                        ))}
                      </ol>
                    )}
                </section>

                <section className="cc-card">
                  <header className="cc-card__head">
                    <h3>Background jobs</h3>
                    <p>How long since each left evidence, against the age at which it is flagged.</p>
                  </header>
                  <ul className="cc-jobs">
                    {[...jobs].sort((a, b) => (a.state === "ok" ? 1 : 0) - (b.state === "ok" ? 1 : 0)).map(j => {
                      const used = j.age_h == null ? 1 : Math.min(1, j.age_h / Math.max(j.stale_after_h, 0.01))
                      const tone: Tone = j.state === "ok" ? (used > 0.75 ? "mid" : "ok") : "bad"
                      return (
                        <li key={j.key} className="cc-job" title={j.why}>
                          <span className={`cc-dot cc-dot--${tone}`} />
                          <span className="cc-job__name">{j.label}</span>
                          <span className="cc-job__age">{ago(j.age_h)}</span>
                          <span className="cc-job__bar" aria-hidden>
                            <span className={`is-${tone}`} style={{ width: `${Math.max(3, used * 100)}%` }} />
                          </span>
                          <span className="cc-job__budget">{j.stale_after_h}h</span>
                        </li>
                      )
                    })}
                  </ul>
                  {!!data.jobs?.queues.length && (
                    <div className="cc-queues">
                      {data.jobs.queues.map(qq => (
                        <div key={qq.key} className="cc-queue">
                          <span className="cc-queue__n">{compact(qq.depth)}</span>
                          <span className="cc-queue__label">{qq.label}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </div>

              <DbActivity compact />
            </>
          )}

          {data && tab === "coverage" && (
            <>
              <ViewHead title="Field coverage"
                sub={<>The share of each archive that <strong>has</strong> each field. Sites under 400,000 works are counted exactly; larger ones are estimated from a sample of {data.coverage_sample.toLocaleString()}.</>}
                explain={<p>A pale cell is not automatically a bug: some fields do not exist at
                  the source, and the ones with a known cause say so below. It becomes
                  a bug when a filter looks like it works and matches almost nothing.
                  Hover a cell for the count.</p>} />
              <section className="cc-card">
                <div className="cc-matrix" style={{ gridTemplateColumns: `minmax(110px, 160px) repeat(${data.coverage.length}, minmax(0, 1fr))` }}>
                  <span />
                  {data.coverage.map(c => (
                    <span key={c.site} className="cc-matrix__col">
                      <strong>{SITE_LABEL[c.site] ?? c.site}</strong>
                      <small>{compact(c.total)} works · {c.exact ? "exact" : "sampled"}</small>
                    </span>
                  ))}
                  {FIELDS.map(f => (
                    <div key={String(f.key)} className="cc-matrix__row">
                      <span className="cc-matrix__field" title={f.why}>{f.label}</span>
                      {data.coverage.map(c => {
                        if (c.na.includes(f.key as string)) return (
                          <span key={c.site} className="cc-matrix__cell is-na" title={`${SITE_LABEL[c.site] ?? c.site} does not publish this field`}>n/a</span>
                        )
                        const h = have(c[f.key] as number, c.sampled)
                        const hasN = Math.round(c.total * (h / 100))
                        return (
                          <span key={c.site}
                            className={`cc-matrix__cell ${h <= 10 ? "is-bad" : h <= 50 ? "is-mid" : "is-ok"}`}
                            style={{ ["--h" as any]: `${h}%` }}
                            title={`${compact(hasN)} of ${compact(c.total)} works have ${f.label.toLowerCase()}. ${f.why}.`}>
                            <span className="cc-matrix__pct">{h}%</span>
                            <span className="cc-matrix__n">{compact(hasN)}</span>
                          </span>
                        )
                      })}
                    </div>
                  ))}
                </div>
              </section>
              <section className="cc-card">
                <header className="cc-card__head"><h3>Known causes</h3></header>
                <ul className="cc-notes">
                  {Object.entries(CAUSE).map(([k, v]) => {
                    const [site, field] = k.split(":")
                    return <li key={k}><strong>{SITE_LABEL[site] ?? site} · {FIELDS.find(f => f.key === field)?.label ?? field}</strong> — {v}</li>
                  })}
                  {data.coverage.filter(c => (c.unreachable_by_listing ?? 0) > 0).map(c => (
                    <li key={c.site + "-unreach"}>
                      <strong>{SITE_LABEL[c.site] ?? c.site} · no fandom</strong> — {c.unreachable_by_listing!.toLocaleString()} rows
                      have no fandom, so the listing harvest (twenty works per request) cannot see
                      them. The stub pass under <button className="linklike" onClick={() => setTab("passes")}>Passes</button> fetches
                      those one at a time, worst gaps first.
                    </li>
                  ))}
                </ul>
              </section>
            </>
          )}

          {data && tab === "crawler" && (
            <>
              <ViewHead title="Crawler" sub="What it fetches next, and how hard each archive is letting it."
                ><Link href="/settings" className="cc-btn">Change in Settings</Link></ViewHead>
              <div className="cc-cols">
                <section className="cc-card">
                  <header className="cc-card__head"><h3>Next up</h3></header>
                  <dl className="cc-kv cc-kv--wide">
                    <dt>Mode</dt>
                    <dd><strong>{data.crawl.mode}</strong>
                      {data.crawl.mode === "pinned" && <span className="cc-warn"> — only your own fandoms gain new works</span>}</dd>
                    <dt>Rotation</dt>
                    <dd>{data.crawl.rotate_count} of {data.crawl.pool} fandoms per pass
                      {data.crawl.cycle_hours != null && <> · full sweep ≈ <strong>{data.crawl.cycle_hours}h</strong></>}</dd>
                    <dt>Direct crawling</dt>
                    <dd>{data.crawl.direct_crawl ? "on" : "off"}
                      {data.crawl.disabled.ao3 && <span className="cc-bad"> · AO3 auto-disabled</span>}
                      {data.crawl.disabled.ffnet && <span className="cc-bad"> · FF.net auto-disabled</span>}</dd>
                    <dt>Queue</dt>
                    <dd className="cc-chips">{data.crawl.upcoming.length
                      ? data.crawl.upcoming.map(f => <span key={f} className="cc-chip">{f}</span>)
                      : <em>nothing configured</em>}</dd>
                  </dl>
                </section>
                <section className="cc-card">
                  <header className="cc-card__head">
                    <h3>Rate limits</h3>
                    <p>Shared across every process, so a maintenance script and the worker queue behind each other.</p>
                  </header>
                  {data.budget.length === 0 ? <p className="cc-empty">No limiter state recorded.</p> : (
                    <ul className="cc-limits">
                      {data.budget.map(b => (
                        <li key={b.host}>
                          <span className={`cc-dot cc-dot--${b.interval_s > 10 ? "bad" : "ok"}`} />
                          <span className="cc-limits__host">{b.host}</span>
                          <span className="cc-limits__rate">one per {b.interval_s}s</span>
                          <span className="cc-limits__q">{b.queued_s > 0 ? `${b.queued_s}s queued` : "idle"}</span>
                          {b.interval_s > 10 && <p className="cc-limits__why">Backed off — the archive has been refusing or throttling us, so this is slow on purpose.</p>}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            </>
          )}

          {data && tab === "storage" && data.storage && (
            <>
              <ViewHead title="Storage"
                sub={<>The database is <strong>{bytes(data.storage.db_bytes)}</strong> on a home box.</>}
                explain={<p>Bars compare each object with the largest, not with the database
                  total: <code>stories</code> is most of it, and against the total every
                  other bar is a stub. Dropping two unused trigram indexes once took the
                  database from 40GB to 36GB — but check CLAUDE.md before dropping
                  anything with zero scans; two of those are load-bearing.</p>} />
              <section className="cc-card">
                <div className="cc-storage">
                  {data.storage.objects.map(o => {
                    const biggest = Math.max(...data.storage!.objects.map(x => x.bytes), 1)
                    return (
                      <div key={o.name} className="cc-storage__row">
                        <span className="cc-storage__name">{o.name}</span>
                        <span className="cc-storage__track"><span style={{ width: `${(o.bytes / biggest) * 100}%` }} /></span>
                        <span className="cc-storage__size">{bytes(o.bytes)}</span>
                        <span className="cc-storage__rows">{o.rows > 0 ? `${compact(o.rows)} rows` : ""}</span>
                      </div>
                    )
                  })}
                </div>
              </section>
            </>
          )}

          {tab === "people" && (
            <>
              <ViewHead title="Accounts"
                sub="Every account, and the column that matters: whether it could recover a password."
                explain={<p>An account with no email address cannot be recovered at all — there
                  is nothing to send a reset to and nothing to check a claim against.
                  Signup did not ask for one until 2026-09-08. Resets are emailed
                  automatically when an account has an address; the button below is
                  the fallback, for an account with none or a request whose email did
                  not go out — you issue a code and pass it on yourself. The list never
                  shows codes: seeing that somebody is locked out and choosing to
                  vouch for them are separate actions.</p>} />
              <section className="cc-card">
                {people === null ? <p className="cc-dim">Loading…</p> : people.length === 0 ? <p className="cc-empty">No accounts yet.</p> : (
                  <table className="cc-table">
                    <thead><tr><th>Account</th><th>Recovery</th><th>Last seen</th><th>Has</th></tr></thead>
                    <tbody>
                      {people.map(u => (
                        <tr key={u.username}>
                          <td><strong>{u.username}</strong>
                            <span className="cc-sub">{u.role}{u.created_at && <> · joined {u.created_at.slice(0, 10)}</>}</span></td>
                          <td>{u.recoverable ? <span className="cc-ok">{u.email}</span> : <span className="cc-bad">no email</span>}</td>
                          <td>{agoStamp(u.last_seen)}
                            <span className="cc-sub">{u.sessions} session{u.sessions === 1 ? "" : "s"}{u.last_login && <> · logged in {u.last_login.slice(0, 10)}</>}</span></td>
                          <td>{[u.saved && `${u.saved} saved`, u.follows && `${u.follows} followed`, u.imported && `${u.imported} imported`]
                            .filter(Boolean).join(" · ") || <span className="cc-dim">—</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
              <section className="cc-card">
                <header className="cc-card__head">
                  <h3>Password resets</h3>
                  <p>Emailed automatically. Issue a code by hand only for a request marked “not sent”, or an account with no email.</p>
                </header>
                {resets && resets.length > 0 ? (
                  <table className="cc-table cc-table--compact">
                    <thead><tr><th>Account</th><th>Email</th><th>Code</th><th>Asked</th><th>Expires</th></tr></thead>
                    <tbody>
                      {resets.map(r => (
                        <tr key={r.username + r.created_at}>
                          <td>{r.username}</td><td>{r.email ?? <em>none on file</em>}</td>
                          {/* Emailed means they already have the code and need
                              nothing from you; only the other rows are waiting
                              on a person. */}
                          <td>{r.emailed ? <span className="cc-ok">emailed</span>
                            : <span className="cc-bad">not sent — needs you</span>}</td>
                          <td>{agoStamp(r.created_at)}</td><td>{new Date(r.expires_at).toLocaleTimeString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : <p className="cc-empty">Nobody is waiting on a password reset.</p>}
                <form className="cc-inline-form" onSubmit={async e => {
                  e.preventDefault()
                  setNote(null); setIssued(null)
                  try {
                    const fd = new FormData()
                    fd.append("username", issuing.trim())
                    const r = await fetchWithTimeout("/api/auth/admin/issue-reset",
                      { method: "POST", credentials: "include", body: fd }, USER_TIMEOUT_MS)
                    const d = await r.json()
                    if (!r.ok) throw new Error(d?.detail ?? `Failed (${r.status})`)
                    setIssued({ username: d.username, code: d.code })
                    setIssuing("")
                  } catch (e: any) { setNote(e.message) }
                }}>
                  <input className="cc-input" value={issuing} placeholder="username" aria-label="Username to issue a reset code for"
                    onChange={e => setIssuing(e.target.value)} />
                  <button className="cc-btn cc-btn--primary" disabled={!issuing.trim()}>Issue a reset code</button>
                </form>
                {issued && (
                  <div className="cc-code">
                    <p>One-time code for <strong>{issued.username}</strong>. It works once, expires
                      shortly and is stored nowhere readable — this is the only time it can be shown.
                      They enter it at <code>/reset</code>; issuing retires any other outstanding code.</p>
                    <code>{issued.code}</code>
                  </div>
                )}
                {note && <p className="cc-msg cc-msg--bad" role="alert">{note}</p>}
              </section>
            </>
          )}
        </div>
      </div>
    </main>
  )
}
