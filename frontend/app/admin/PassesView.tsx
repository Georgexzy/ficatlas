"use client"

// The maintenance console.
//
// Driven by GET /api/admin/jobs, which returns the registry AND each pass's last
// outcome AND whether the worker is listening — so the page shows real state on
// load, not only for passes started in this tab.
//
// The passes RUN ON THE WORKER now (see backend/admin_jobs.py). The panel used
// to start them inside whichever API process took the click and keep their state
// in that process's memory, which with WEB_CONCURRENCY=4 meant three polls in
// four reported "lost track of it — the worker restarted" about a pass that was
// fine, and a deploy killed any long pass outright.
//
// PREVIEW IS THE DEFAULT. Every pass implements `dry_run`, so the first click
// answers "what would this do?" without touching a row, and committing is a
// second, explicit click.

import { useCallback, useEffect, useRef, useState } from "react"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

type Phase = "never-run" | "queued" | "running" | "done" | "failed" | "deferred" | "cancelled"
interface Last {
  job: string; state: Phase; dry_run?: boolean; limit?: number
  requested_at?: number; started_at?: number; finished_at?: number
  report?: string; error?: string; stalled?: boolean
}
interface Pass { key: string; label: string; blurb: string; networked: boolean; minutes: number; last: Last }
interface Jobs { jobs: Pass[]; history: Last[]; worker_seen_s: number | null; worker_silent_after_s: number }

function since(t?: number): string {
  if (!t) return ""
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 90) return `${Math.round(s)}s ago`
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172800) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}
function took(a?: number, b?: number): string {
  if (!a || !b) return ""
  const s = b - a
  return s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`
}

export default function PassesView({ onCounts }: { onCounts?: (active: number) => void }) {
  const [data, setData] = useState<Jobs | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [limit, setLimit] = useState("200")
  const [pending, setPending] = useState<Record<string, string>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetchWithTimeout("/api/admin/jobs", { credentials: "include" }, USER_TIMEOUT_MS)
      if (!r.ok) throw new Error(`Could not load the passes (${r.status})`)
      const d: Jobs = await r.json()
      setData(d); setErr(null)
      onCounts?.(d.jobs.filter(j => j.last.state === "queued" || j.last.state === "running").length)
      return d
    } catch (e: any) { setErr(e?.message ?? "Could not load the passes."); return null }
  }, [onCounts])

  // Poll while anything is in flight, slowly otherwise. Clear before re-arming:
  // a poll that re-arms itself without cancelling is a fork (CLAUDE.md, the
  // HealthBanner incident).
  const schedule = useCallback((d: Jobs | null) => {
    if (timer.current) clearTimeout(timer.current)
    const busy = d?.jobs.some(j => j.last.state === "queued" || j.last.state === "running")
    timer.current = setTimeout(async () => schedule(await load()), busy ? 3000 : 30000)
  }, [load])

  useEffect(() => {
    load().then(schedule)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [load, schedule])

  const act = async (key: string, what: "preview" | "run" | "cancel") => {
    setPending(p => ({ ...p, [key]: what })); setNotes(n => ({ ...n, [key]: "" }))
    try {
      let r: Response
      if (what === "cancel") {
        r = await fetchWithTimeout(`/api/admin/run/${key}/cancel`,
          { method: "POST", credentials: "include" }, USER_TIMEOUT_MS)
      } else {
        const fd = new FormData()
        fd.append("limit", String(Math.min(Math.max(Number(limit) || 200, 1), 2000)))
        fd.append("dry_run", what === "preview" ? "true" : "false")
        r = await fetchWithTimeout(`/api/admin/run/${key}`,
          { method: "POST", body: fd, credentials: "include" }, USER_TIMEOUT_MS)
      }
      const d = await r.json().catch(() => null)
      if (!r.ok) throw new Error(d?.detail ?? `HTTP ${r.status}`)
    } catch (e: any) {
      setNotes(n => ({ ...n, [key]: e?.message ?? "failed" }))
    } finally {
      setPending(p => { const { [key]: _, ...rest } = p; return rest })
      schedule(await load())
    }
  }

  if (err && !data) return <p className="cc-msg cc-msg--bad">{err}</p>
  if (!data) return <p className="cc-dim">Loading the passes…</p>

  const silent = data.worker_seen_s == null || data.worker_seen_s > data.worker_silent_after_s
  return (
    <div className="cc-stack">
      <div className={`cc-banner ${silent ? "cc-banner--bad" : "cc-banner--ok"}`}>
        <span className={`cc-dot cc-dot--${silent ? "bad" : "ok"}`} />
        {silent ? (
          <span>
            <strong>Nothing is picking passes up.</strong>{" "}
            {data.worker_seen_s == null
              ? "The worker's pass runner has never checked in — it is running code from before the runner existed, or RUN_ADMIN_JOBS is off."
              : `The worker last checked ${Math.round(data.worker_seen_s / 60)} minutes ago.`}{" "}
            Anything queued here will wait until it does.
          </span>
        ) : (
          <span>Worker listening — last checked {data.worker_seen_s}s ago. Passes run there, not in the web process, so a deploy cannot kill one.</span>
        )}
        <label className="cc-banner__limit">
          Max rows per run
          <input className="cc-input cc-input--num" type="number" min={1} max={2000}
            value={limit} onChange={e => setLimit(e.target.value)} />
        </label>
      </div>

      <div className="cc-passes">
        {data.jobs.map(j => {
          const L = j.last
          const inflight = L.state === "queued" || L.state === "running"
          const p = pending[j.key]
          return (
            <article key={j.key} className={`cc-pass cc-pass--${L.state}`}>
              <header className="cc-pass__head">
                <h4>{j.label}</h4>
                <span className="cc-pass__tags">
                  <span className="cc-tag">{j.minutes > 0 ? `~${j.minutes >= 60 ? `${Math.round(j.minutes / 60)}h` : `${j.minutes}m`}` : "quick"}</span>
                  {j.networked && <span className="cc-tag cc-tag--net" title="Makes outbound requests, so it shares a rate limiter with the crawler">networked</span>}
                </span>
              </header>
              <p className="cc-pass__blurb">{j.blurb}</p>

              <div className={`cc-pass__state cc-pass__state--${L.state}`}>
                {L.state === "never-run" && <span className="cc-dim">Not run from here yet.</span>}
                {L.state === "queued" && <span>{L.stalled ? "Queued — but no worker is listening." : "Queued — the worker picks it up within 10s."}</span>}
                {L.state === "running" && <span><span className="cc-spin" aria-hidden /> {L.dry_run ? "Previewing" : "Running for real"} · started {since(L.started_at)}</span>}
                {L.state === "done" && <span><strong>{L.dry_run ? "Would do:" : "Did:"}</strong> {L.report} <span className="cc-dim">· {since(L.finished_at)}, took {took(L.started_at, L.finished_at)}</span></span>}
                {L.state === "failed" && <span><strong>Failed:</strong> {L.error} <span className="cc-dim">· {since(L.finished_at)}</span></span>}
                {L.state === "deferred" && <span><strong>Deferred:</strong> {L.error}. Try again when it finishes. <span className="cc-dim">· {since(L.finished_at)}</span></span>}
                {L.state === "cancelled" && <span className="cc-dim">Withdrawn {since(L.finished_at)}.</span>}
              </div>
              {notes[j.key] && <p className="cc-msg cc-msg--bad">{notes[j.key]}</p>}

              <div className="cc-pass__actions">
                {L.state === "queued" ? (
                  <button className="cc-btn" disabled={!!p} onClick={() => act(j.key, "cancel")}>Withdraw</button>
                ) : (
                  <>
                    <button className="cc-btn" disabled={inflight || !!p} onClick={() => act(j.key, "preview")}>
                      {p === "preview" ? "Queueing…" : "Preview"}
                    </button>
                    <button className="cc-btn cc-btn--warn" disabled={inflight || !!p} onClick={() => act(j.key, "run")}>
                      {p === "run" ? "Queueing…" : "Run for real"}
                    </button>
                  </>
                )}
              </div>
            </article>
          )
        })}
      </div>

      {data.history.length > 0 && (
        <section className="cc-card">
          <header className="cc-card__head"><h3>Recent runs</h3></header>
          <table className="cc-table cc-table--compact">
            <thead><tr><th>Pass</th><th>Kind</th><th>Outcome</th><th>Finished</th><th className="num">Took</th></tr></thead>
            <tbody>
              {data.history.map((h, i) => (
                <tr key={i} className={h.state === "failed" ? "is-bad" : ""}>
                  <td>{data.jobs.find(j => j.key === h.job)?.label ?? h.job}</td>
                  <td>{h.dry_run ? "preview" : <strong>real</strong>}</td>
                  <td className="cc-wrap">{h.state === "done" ? h.report : `${h.state}${h.error ? `: ${h.error}` : ""}`}</td>
                  <td>{since(h.finished_at)}</td>
                  <td className="num">{took(h.started_at, h.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <p className="cc-foot">
        A preview writes nothing and is always the first button. Heavy repairs
        take turns through one shared lock, so a second one started while another
        runs reports <em>deferred</em> instead of colliding with it.
      </p>
    </div>
  )
}
