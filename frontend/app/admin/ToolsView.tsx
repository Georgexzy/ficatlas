"use client"

// The control centre's hands-on tools.
//
// Each of these answers a question that, before this view, was answered in a
// terminal — and every one of them has a measured incident in CLAUDE.md behind
// it:
//
//   "is it the search or the box?"     → DbActivity. Three slow-site hunts ended
//                                         at a periodic full scan nobody thought
//                                         of as a query.
//   "why did this work turn up?"       → StoryInspector. The gate columns are a
//                                         CACHE of gate_terms; this says, per
//                                         work, whether the cache still agrees.
//   "did that deploy break search?"    → SearchProbe's canaries, the queries this
//                                         repo already uses as regression checks.
//   "is the public site answering?"    → EndpointCheck, from the reader's side of
//                                         the proxy, not the container's.
//
// None of them writes anything. Probe searches are made with the owner's
// session, which search() marks as staff, so they never reach the Audience
// figures — the developer-traffic problem recorded in CLAUDE.md three times.

import Link from "next/link"
import { useCallback, useEffect, useRef, useState } from "react"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

const SITE: Record<string, string> = { ao3: "AO3", ffnet: "FF.net", fictionalley: "FictionAlley" }

function ms(n: number) { return n >= 1000 ? `${(n / 1000).toFixed(2)}s` : `${Math.round(n)}ms` }
function speed(n: number) { return n < 1500 ? "ok" : n < 5000 ? "mid" : "bad" }

// ── story inspector ─────────────────────────────────────────────────────────
interface Inspected {
  id: string; site: string; site_id: string; url: string; title: string; author: string | null
  rating: string | null; status: string | null; language: string | null
  word_count: number | null; chapter_count: number | null; chapter_count_total: number | null
  kudos: number | null; bookmarks: number | null; hits: number | null; comments: number | null
  popularity: number | null; is_crossover: boolean; crossover_now: boolean
  series_total_words: number | null
  fandoms: string[] | null; relationships: string[] | null; characters: string[] | null
  tags: string[] | null; warnings: string[] | null
  is_hosted: boolean; delisted_at: string | null; delisted_reason: string | null
  text_withdrawn_at: string | null
  published_at: string | null; updated_at: string | null; crawled_at: string | null; indexed_at: string | null
  has_summary: boolean; chapters_stored: number
  gates: Record<"underage" | "adult", { column: boolean | null; lists: boolean; terms: string[] }>
  series: { id: string; title: string; position: number | null; member_count: number | null; total_words: number | null }[]
}

function StoryInspector() {
  const [q, setQ] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [s, setS] = useState<Inspected | null>(null)

  const go = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!q.trim()) return
    setBusy(true); setErr(null)
    try {
      const r = await fetchWithTimeout(`/api/admin/story?q=${encodeURIComponent(q.trim())}`,
        { credentials: "include" }, USER_TIMEOUT_MS)
      const d = await r.json().catch(() => null)
      if (!r.ok) throw new Error(d?.detail ?? `Lookup failed (${r.status})`)
      setS(d)
    } catch (e: any) { setErr(e.message); setS(null) }
    finally { setBusy(false) }
  }

  const drift = s && (["underage", "adult"] as const).filter(k => !!s.gates[k].column !== s.gates[k].lists)
  const fmt = (n: number | null | undefined) => n == null ? "—" : n.toLocaleString()
  const day = (v: string | null) => v ? v.slice(0, 10) : "—"

  return (
    <section className="cc-card cc-tool">
      <header className="cc-card__head">
        <h3>Story inspector</h3>
        <p>Paste a story id, an AO3 or FanFiction.net URL, or <code>ao3:123</code>.</p>
      </header>
      <form className="cc-inline-form" onSubmit={go}>
        <input className="cc-input" value={q} onChange={e => setQ(e.target.value)}
          placeholder="https://archiveofourown.org/works/…" aria-label="Story to inspect" />
        <button className="cc-btn cc-btn--primary" disabled={busy || !q.trim()}>
          {busy ? "Looking…" : "Inspect"}
        </button>
      </form>
      {err && <p className="cc-msg cc-msg--bad" role="alert">{err}</p>}
      {s && (
        <div className="cc-inspect">
          <div className="cc-inspect__title">
            <strong>{s.title}</strong>
            <span>{s.author ?? "unknown author"} · {SITE[s.site] ?? s.site} #{s.site_id}</span>
            <span className="cc-inspect__links">
              <Link href={`/story/${s.id}`}>Story page</Link>
              <a href={s.url} target="_blank" rel="noreferrer">On {SITE[s.site] ?? s.site} ↗</a>
            </span>
          </div>

          {drift && drift.length > 0 && (
            <p className="cc-msg cc-msg--bad">
              The cached gate column disagrees with the term lists for{" "}
              <strong>{drift.join(" and ")}</strong>. The search path still hides
              it by the lists, but hubs and counts read the column — run{" "}
              <em>Recompute the content gates</em> under Passes.
            </p>
          )}

          <div className="cc-inspect__grid">
            <dl className="cc-kv">
              <dt>Rating</dt><dd>{s.rating ?? "—"}</dd>
              <dt>Status</dt><dd>{s.status ?? "—"}</dd>
              <dt>Language</dt><dd>{s.language ?? "—"}</dd>
              <dt>Words</dt><dd>{fmt(s.word_count)}</dd>
              <dt>Chapters</dt>
              <dd>{fmt(s.chapter_count)}{s.chapter_count_total ? ` of ${s.chapter_count_total}` : ""}
                {s.chapters_stored > 0 && <span className="cc-dim"> · {s.chapters_stored} stored</span>}</dd>
              <dt>Summary</dt><dd>{s.has_summary ? "yes" : <span className="cc-warn">none</span>}</dd>
            </dl>
            <dl className="cc-kv">
              <dt>Kudos</dt><dd>{fmt(s.kudos)}</dd>
              <dt>Bookmarks</dt><dd>{fmt(s.bookmarks)}</dd>
              <dt>Hits</dt><dd>{fmt(s.hits)}</dd>
              <dt>Popularity</dt>
              <dd>{s.popularity == null ? <span className="cc-dim">unscored</span> : s.popularity.toFixed(4)}</dd>
              <dt>Crossover</dt>
              <dd>{s.is_crossover ? "yes" : "no"}
                {s.is_crossover !== s.crossover_now && <span className="cc-warn"> · rule says {s.crossover_now ? "yes" : "no"}</span>}</dd>
              <dt>Series total</dt><dd>{fmt(s.series_total_words)}</dd>
            </dl>
            <dl className="cc-kv">
              <dt>Published</dt><dd>{day(s.published_at)}</dd>
              <dt>Updated</dt><dd>{day(s.updated_at)}</dd>
              <dt>Crawled</dt><dd>{day(s.crawled_at)}</dd>
              <dt>Indexed</dt><dd>{day(s.indexed_at)}</dd>
              <dt>Hosted</dt><dd>{s.is_hosted ? "yes" : "no"}</dd>
              <dt>Delisted</dt>
              <dd>{s.delisted_at ? <span className="cc-warn">{day(s.delisted_at)} · {s.delisted_reason ?? "no reason"}</span> : "no"}</dd>
            </dl>
          </div>

          <table className="cc-table cc-table--compact">
            <thead><tr><th>Gate</th><th>Column</th><th>Term lists</th><th>Matching terms</th></tr></thead>
            <tbody>
              {(["underage", "adult"] as const).map(k => {
                const g = s.gates[k]
                const bad = !!g.column !== g.lists
                return (
                  <tr key={k} className={bad ? "is-bad" : ""}>
                    <td>{k === "underage" ? "Tier 1 · underage" : "Tier 2 · adult"}</td>
                    <td>{g.column ? "gated" : "open"}</td>
                    <td>{g.lists ? "gated" : "open"}</td>
                    <td>{g.terms.length ? g.terms.join(", ") : <span className="cc-dim">none</span>}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="cc-taglines">
            {[["Fandoms", s.fandoms], ["Pairings", s.relationships], ["Characters", s.characters],
              ["Warnings", s.warnings], ["Tags", s.tags]].map(([label, vals]) =>
              (vals as string[] | null)?.length ? (
                <p key={label as string}>
                  <span className="cc-taglines__k">{label as string}</span>
                  {(vals as string[]).slice(0, 30).join(" · ")}
                  {(vals as string[]).length > 30 && <span className="cc-dim"> · +{(vals as string[]).length - 30} more</span>}
                </p>
              ) : null)}
          </div>

          {s.series.length > 0 && (
            <p className="cc-dim">
              In series:{" "}
              {s.series.map(x => (
                <span key={x.id}>
                  <Link href={`/series/${x.id}`}>{x.title}</Link>
                  {x.position != null && ` #${x.position}`}
                  {x.member_count != null && ` (${x.member_count} indexed)`}{" "}
                </span>
              ))}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

// ── search probe ────────────────────────────────────────────────────────────
// The canaries are the queries CLAUDE.md already uses to check a ranking or
// performance change: each is there because it once broke. Expectations are
// deliberately loose — "returns something, in under the statement timeout" —
// because a table of exact counts is a test of the vocabulary, not the search.
const CANARIES = [
  "all the young dudes", "drarry", "harry potter", "fluff", "angst",
  "coffee shop au", "fandom:Naruto time travel", "the arithmancer",
]

interface ProbeRow { q: string; ms: number; total: number | null; capped: boolean
                     split: Record<string, number>; top: string | null; error?: string }

async function probe(q: string): Promise<ProbeRow> {
  const t0 = performance.now()
  try {
    const r = await fetchWithTimeout(`/api/search?q=${encodeURIComponent(q)}&per_page=20`,
      { credentials: "include" }, 30000)
    const d = await r.json().catch(() => null)
    const dt = performance.now() - t0
    if (!r.ok) return { q, ms: dt, total: null, capped: false, split: {}, top: null,
                        error: d?.detail ?? `HTTP ${r.status}` }
    const split: Record<string, number> = {}
    for (const it of d?.results ?? []) split[it.site] = (split[it.site] ?? 0) + 1
    return { q, ms: dt, total: d?.total ?? null, capped: !!d?.count_is_capped, split,
             top: d?.results?.[0]?.title ?? null }
  } catch (e: any) {
    return { q, ms: performance.now() - t0, total: null, capped: false, split: {}, top: null,
             error: e?.name === "AbortError" ? "timed out" : (e?.message ?? "failed") }
  }
}

function SearchProbe() {
  const [q, setQ] = useState("")
  const [rows, setRows] = useState<ProbeRow[]>([])
  const [busy, setBusy] = useState(false)

  const run = async (qs: string[]) => {
    setBusy(true)
    setRows([])
    // Sequential, on purpose: eight searches at once would measure the box
    // under a load no reader produces, and on a slow day would itself be the
    // thing that makes the site slow.
    for (const one of qs) {
      const row = await probe(one)
      setRows(prev => [...prev, row])
    }
    setBusy(false)
  }

  return (
    <section className="cc-card cc-tool">
      <header className="cc-card__head">
        <h3>Search probe</h3>
        <p>Time a query end to end through the public API. Your searches are not
          counted as reader traffic.</p>
      </header>
      <form className="cc-inline-form" onSubmit={e => { e.preventDefault(); if (q.trim()) run([q.trim()]) }}>
        <input className="cc-input" value={q} onChange={e => setQ(e.target.value)}
          placeholder="harry potter time travel" aria-label="Query to probe" />
        <button className="cc-btn cc-btn--primary" disabled={busy || !q.trim()}>Probe</button>
        <button type="button" className="cc-btn" disabled={busy} onClick={() => run(CANARIES)}>
          Run {CANARIES.length} canaries
        </button>
      </form>
      {rows.length > 0 && (
        <table className="cc-table cc-table--compact">
          <thead><tr><th>Query</th><th className="num">Time</th><th className="num">Works</th>
            <th>Page 1 by archive</th><th>Top result</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.q + i} className={r.error || r.total === 0 ? "is-bad" : ""}>
                <td>
                  <Link href={`/?q=${encodeURIComponent(r.q)}`} target="_blank">{r.q}</Link>
                </td>
                <td className="num"><span className={`cc-speed cc-speed--${speed(r.ms)}`}>{ms(r.ms)}</span></td>
                <td className="num">{r.error ? <span className="cc-bad">{r.error}</span>
                  : r.total == null ? "—" : `${r.total.toLocaleString()}${r.capped ? "+" : ""}`}</td>
                <td>
                  <span className="cc-split">
                    {Object.entries(r.split).map(([k, v]) => (
                      <span key={k} className={`cc-split__seg cc-split__seg--${k}`}
                        style={{ flexGrow: v }} title={`${SITE[k] ?? k}: ${v}`}>{v}</span>
                    ))}
                  </span>
                </td>
                <td className="cc-ellipsis">{r.top ?? "—"}</td>
              </tr>
            ))}
            {busy && <tr><td colSpan={5} className="cc-dim">running…</td></tr>}
          </tbody>
        </table>
      )}
      {rows.length > 0 && (
        <p className="cc-legend">
          <span className="cc-split__seg cc-split__seg--ao3">AO3</span>
          <span className="cc-split__seg cc-split__seg--ffnet">FF.net</span>
          <span className="cc-split__seg cc-split__seg--fictionalley">FictionAlley</span>
          · results cache for fifteen minutes, so a repeat is a warm read.
        </p>
      )}
    </section>
  )
}

// ── endpoint check ──────────────────────────────────────────────────────────
// Same origin, through the same proxy a reader goes through. A container that
// says healthy and a page that 502s are both true at once surprisingly often
// here — the worktree deploy note in CLAUDE.md is the latest instance.
const ENDPOINTS: { label: string; path: string }[] = [
  { label: "Home", path: "/" },
  { label: "Index totals", path: "/api/stats/totals" },
  { label: "A ship hub", path: "/ship/draco-malfoy-harry-potter" },
  { label: "A fandom hub", path: "/fandom/naruto" },
  { label: "Fandom index", path: "/fandoms" },
  { label: "Sitemap index", path: "/sitemap.xml" },
  { label: "robots.txt", path: "/robots.txt" },
  { label: "Offline shell", path: "/story/offline-shell" },
  { label: "Service worker", path: "/sw.js" },
]

function EndpointCheck() {
  const [rows, setRows] = useState<{ label: string; path: string; status: number | null; ms: number; bytes: number | null }[]>([])
  const [busy, setBusy] = useState(false)
  const run = useCallback(async () => {
    setBusy(true)
    const out = await Promise.all(ENDPOINTS.map(async e => {
      const t0 = performance.now()
      try {
        const r = await fetchWithTimeout(e.path, { cache: "no-store" }, 20000)
        const b = await r.arrayBuffer()
        return { ...e, status: r.status, ms: performance.now() - t0, bytes: b.byteLength }
      } catch {
        return { ...e, status: null, ms: performance.now() - t0, bytes: null }
      }
    }))
    setRows(out); setBusy(false)
  }, [])
  useEffect(() => { run() }, [run])
  return (
    <section className="cc-card cc-tool">
      <header className="cc-card__head">
        <h3>Public endpoints</h3>
        <p>Fetched from this browser, uncached, through the same proxy a reader uses.</p>
        <button className="cc-btn cc-btn--small" onClick={run} disabled={busy}>{busy ? "Checking…" : "Check again"}</button>
      </header>
      <ul className="cc-endpoints">
        {rows.map(r => (
          <li key={r.path}>
            <span className={`cc-dot cc-dot--${r.status && r.status < 400 ? speed(r.ms) : "bad"}`} />
            <a href={r.path} target="_blank" rel="noreferrer">{r.label}</a>
            <code>{r.path}</code>
            <span className="cc-endpoints__status">{r.status ?? "no answer"}</span>
            <span className="cc-endpoints__ms">{ms(r.ms)}</span>
            <span className="cc-endpoints__size">{r.bytes == null ? "" : r.bytes > 1e6 ? `${(r.bytes / 1e6).toFixed(1)} MB` : `${Math.round(r.bytes / 1024)} KB`}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ── database activity ───────────────────────────────────────────────────────
export interface Activity {
  connections: { total: number; active: number; idle_in_xact: number; max: number }
  running: { pid: number; application_name: string; state: string; wait_event_type: string | null
             wait_event: string | null; running_s: number; xact_s: number; query: string
             blocked_by: number }[]
  waiting_on_locks: number
  longest_s: number
}

export function DbActivity({ compact = false }: { compact?: boolean }) {
  const [a, setA] = useState<Activity | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [live, setLive] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetchWithTimeout("/api/admin/activity", { credentials: "include" }, USER_TIMEOUT_MS)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setA(await r.json()); setErr(null)
    } catch (e: any) { setErr(e.message) }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    // Cancel before re-arming, or every toggle forks a second poll chain —
    // the HealthBanner bug recorded in CLAUDE.md.
    if (timer.current) clearTimeout(timer.current)
    if (!live) return
    const tick = async () => { await load(); timer.current = setTimeout(tick, 5000) }
    timer.current = setTimeout(tick, 5000)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [live, load])

  const c = a?.connections
  return (
    <section className="cc-card cc-tool">
      <header className="cc-card__head">
        <h3>Database right now</h3>
        <p>Every non-idle query, longest first. When search is slow, look here for
          what <em>else</em> is reading <code>stories</code>.</p>
        <label className="cc-toggle">
          <input type="checkbox" checked={live} onChange={e => setLive(e.target.checked)} />
          live (5s)
        </label>
      </header>
      {err && <p className="cc-msg cc-msg--bad">{err}</p>}
      {c && (
        <div className="cc-meters">
          <div className="cc-meter">
            <span className="cc-meter__k">Connections</span>
            <span className="cc-meter__v">{c.total}<small> / {c.max}</small></span>
            <span className="cc-meter__bar"><span style={{ width: `${Math.min(100, (c.total / c.max) * 100)}%` }}
              className={c.total / c.max > 0.8 ? "is-bad" : c.total / c.max > 0.6 ? "is-mid" : ""} /></span>
          </div>
          <div className="cc-meter">
            <span className="cc-meter__k">Active</span>
            <span className="cc-meter__v">{c.active}</span>
          </div>
          <div className="cc-meter">
            <span className="cc-meter__k">Waiting on a lock</span>
            <span className={`cc-meter__v ${a!.waiting_on_locks ? "cc-bad" : ""}`}>{a!.waiting_on_locks}</span>
          </div>
          <div className="cc-meter">
            <span className="cc-meter__k">Idle in transaction</span>
            <span className={`cc-meter__v ${c.idle_in_xact ? "cc-warn" : ""}`}>{c.idle_in_xact}</span>
          </div>
          <div className="cc-meter">
            <span className="cc-meter__k">Longest query</span>
            <span className={`cc-meter__v ${a!.longest_s > 30 ? "cc-warn" : ""}`}>{a!.longest_s ? `${a!.longest_s}s` : "—"}</span>
          </div>
        </div>
      )}
      {a && !compact && (
        a.running.length === 0
          ? <p className="cc-dim">Nothing running but this page's own question.</p>
          : (
            <table className="cc-table cc-table--compact cc-table--code">
              <thead><tr><th className="num">For</th><th>Waiting on</th><th>Query</th></tr></thead>
              <tbody>
                {a.running.map(r => (
                  <tr key={r.pid} className={r.blocked_by ? "is-bad" : r.running_s > 30 ? "is-mid" : ""}>
                    <td className="num">{r.running_s}s</td>
                    <td>{r.blocked_by ? `lock (${r.blocked_by})` : r.wait_event ? `${r.wait_event_type}:${r.wait_event}` : "cpu"}</td>
                    <td><code title={r.query}>{r.query}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
      )}
    </section>
  )
}

export default function ToolsView() {
  return (
    <div className="cc-stack">
      <StoryInspector />
      <SearchProbe />
      <div className="cc-cols">
        <EndpointCheck />
        <DbActivity />
      </div>
    </div>
  )
}
