"use client"

import { useCallback, useEffect, useState } from "react"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

type Pending = { id: string; restart_at: number }
type Status = { available: boolean; pending: Pending | null; bridge_status?: { state?: string; returncode?: number } | null }

function defaultRestartTime() {
  const date = new Date(Math.ceil((Date.now() + 5 * 60_000) / 60_000) * 60_000)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function remaining(at: number, now: number) {
  const seconds = Math.max(0, Math.ceil(at - now))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

export default function RestartPanel() {
  const [status, setStatus] = useState<Status | null>(null)
  const [now, setNow] = useState(Date.now() / 1000)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [restartTime, setRestartTime] = useState(defaultRestartTime)

  const refresh = useCallback(async () => {
    try {
      const r = await fetchWithTimeout("/api/admin/restart", { credentials: "include" }, USER_TIMEOUT_MS)
      const data = await r.json().catch(() => null)
      if (!r.ok) throw new Error(data?.detail || `Could not read restart status (${r.status}).`)
      setStatus(data)
      setError("")
    } catch (e: any) {
      setError(e?.message || "Could not read restart status.")
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Date.now() / 1000)
      refresh()
    }, 5000)
    return () => window.clearInterval(id)
  }, [refresh])

  const schedule = async () => {
    if (!window.confirm("Publish a five-minute maintenance notice, then reboot the FicAtlas server computer?")) return
    setBusy(true); setError(""); setMessage("")
    try {
      const r = await fetchWithTimeout("/api/admin/restart", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, restart_at: new Date(restartTime).getTime() / 1000 }),
      }, USER_TIMEOUT_MS)
      const data = await r.json().catch(() => null)
      if (!r.ok) throw new Error(data?.detail || `Could not schedule restart (${r.status}).`)
      setStatus({ available: true, pending: data.pending })
      setMessage("Restart scheduled. The announcement will appear five minutes before the reboot.")
      window.dispatchEvent(new CustomEvent("ficatlas:site-notice-updated", {
        detail: { message: data.pending.announcement, startsAt: data.pending.announcement_start_at, until: data.pending.announcement_until },
      }))
    } catch (e: any) { setError(e?.message || "Could not schedule restart.") }
    finally { setBusy(false) }
  }

  const cancel = async () => {
    if (!window.confirm("Cancel the scheduled server restart and remove its announcement?")) return
    setBusy(true); setError(""); setMessage("")
    try {
      const r = await fetchWithTimeout("/api/admin/restart/cancel", {
        method: "POST", credentials: "include",
      }, USER_TIMEOUT_MS)
      const data = await r.json().catch(() => null)
      if (!r.ok) throw new Error(data?.detail || `Could not cancel restart (${r.status}).`)
      setStatus(s => s ? { ...s, pending: null } : s)
      setMessage("Restart cancelled. The previous announcement has been restored.")
      await refresh()
      window.dispatchEvent(new CustomEvent("ficatlas:site-notice-updated", {
        detail: { message: data.site_notice || "", startsAt: data.site_notice_start_at || "", until: data.site_notice_until || "" },
      }))
    } catch (e: any) { setError(e?.message || "Could not cancel restart.") }
    finally { setBusy(false) }
  }

  return <section className="cc-card">
    <header className="cc-card__head">
      <h3>Restart the FicAtlas server</h3>
      <p>Schedule a host computer reboot. Readers see a site-wide notice five minutes beforehand; Docker brings FicAtlas back up after the host returns.</p>
    </header>
    {error && <p className="cc-msg cc-msg--bad" role="alert">{error}</p>}
    {message && <p className="cc-msg" role="status">{message}</p>}
    {status === null ? <p className="cc-dim">Checking the host restart service…</p> : !status.available ? <>
      <p className="cc-msg cc-msg--bad" role="status">Host restart service is not installed or available. The schedule button is disabled.</p>
      <p className="cc-dim">On the server computer, run <code>sudo bash deploy/install-restart-bridge.sh</code> from the FicAtlas checkout. This grants only the fixed reboot action to the host bridge.</p>
    </> : <>
      {status.bridge_status?.state === "reboot-command-failed" && <p className="cc-msg cc-msg--bad" role="alert">
        The host refused the reboot command (exit {status.bridge_status.returncode ?? "unknown"}). Check
        <code> sudo journalctl -u ficatlas-restart-bridge.service -n 50</code> before retrying.
      </p>}
      {status.pending ? <>
      <p className="cc-msg" role="status">
        Restart scheduled. The server will restart in <strong>{remaining(status.pending.restart_at, now)}</strong>.
      </p>
      <button type="button" className="btn" disabled={busy || status.pending.restart_at <= now}
        onClick={cancel}>{busy ? "Working…" : "Cancel scheduled restart"}</button>
      </> : <>
      <p className="cc-dim">Choose a time at least five minutes from now (up to 30 days). The announcement starts five minutes before it. You can cancel any time before reboot begins.</p>
      <label className="setting-row"><span className="setting-row__name">Restart time (your local time)</span>
        <input aria-label="Restart time" className="setting-input" type="datetime-local" min={defaultRestartTime()}
          value={restartTime} onChange={e => setRestartTime(e.target.value)} />
      </label>
      <button type="button" className="btn btn--primary" disabled={busy || !restartTime} onClick={schedule}>
        {busy ? "Scheduling…" : "Schedule announced host restart"}
      </button>
      </>}
    </>}
  </section>
}
