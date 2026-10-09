"use client"

import { useEffect, useMemo, useState } from "react"
import { fetchJson } from "@/lib/errors"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

type Values = {
  tracked_fandom: string; crawl_mode: string; crawl_rotate_count: string
  poll_on_load: string; live_fetch: string; feed_min_words: string
  feed_max_words: string; feed_complete_only: string; enable_direct_crawl: string
  site_notice: string
}
const KEYS = Object.keys({
  tracked_fandom: 1, crawl_mode: 1, crawl_rotate_count: 1, poll_on_load: 1,
  live_fetch: 1, feed_min_words: 1, feed_max_words: 1, feed_complete_only: 1,
  enable_direct_crawl: 1, site_notice: 1,
}) as (keyof Values)[]
const EMPTY: Values = {
  tracked_fandom: "", crawl_mode: "mixed", crawl_rotate_count: "3",
  poll_on_load: "true", live_fetch: "true", feed_min_words: "",
  feed_max_words: "", feed_complete_only: "false", enable_direct_crawl: "false",
  site_notice: "",
}

function Switch({ value, label, onChange }: {
  value: boolean; label: string; onChange: (v: boolean) => void
}) {
  return <button type="button" className={`switch ${value ? "switch--on" : ""}`}
    role="switch" aria-checked={value} aria-label={label} onClick={() => onChange(!value)}>
    <span className="switch__thumb" />
  </button>
}

/** Instance-wide controls belong with operations, not beside a reader's
 * preferences. All values are loaded together and saved with explicit feedback. */
export default function ConfigView() {
  const [values, setValues] = useState<Values | null>(null)
  const [saved, setSaved] = useState<Values | null>(null)
  const [autoDisabled, setAutoDisabled] = useState({ ao3: false, ffnet: false })
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const dirty = useMemo(() => !!values && !!saved && KEYS.some(k => values[k] !== saved[k]), [values, saved])

  useEffect(() => {
    let live = true
    fetchJson("/api/settings").then(data => {
      if (!live) return
      const loaded = Object.fromEntries(KEYS.map(k => [k, data[k] ?? EMPTY[k]])) as Values
      setValues(loaded); setSaved(loaded)
    }).catch(e => { if (live) setMessage(e?.message ?? "Could not load site settings.") })
    fetchJson("/api/library/crawl-status").then(d => {
      if (live) setAutoDisabled(d.auto_disabled || { ao3: false, ffnet: false })
    }).catch(() => {})
    return () => { live = false }
  }, [])

  const set = (key: keyof Values, value: string) => setValues(v => v ? { ...v, [key]: value } : v)
  const save = async () => {
    if (!values || !dirty) return
    setSaving(true); setMessage("")
    try {
      const changed = Object.fromEntries(KEYS.filter(k => values[k] !== saved?.[k]).map(k => [k, values[k]]))
      const r = await fetchWithTimeout("/api/settings/bulk", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values: changed }),
      }, USER_TIMEOUT_MS)
      const data = await r.json().catch(() => null)
      if (!r.ok) throw new Error(data?.detail ?? `Could not save settings (${r.status}).`)
      setSaved(values); setMessage("Settings saved.")
      window.dispatchEvent(new CustomEvent("ficatlas:site-notice-updated", { detail: values.site_notice }))
    } catch (e: any) { setMessage(e?.message ?? "Could not save settings.") }
    finally { setSaving(false) }
  }

  const resetBreaker = async (site: "ao3" | "ffnet") => {
    const form = new FormData(); form.append("site", site)
    try {
      const r = await fetchWithTimeout("/api/library/crawl-reset-breaker", { method: "POST", body: form, credentials: "include" }, USER_TIMEOUT_MS)
      if (!r.ok) throw new Error(`Could not re-enable ${site === "ao3" ? "AO3" : "FF.net"} (${r.status}).`)
      setAutoDisabled(v => ({ ...v, [site]: false }))
      setMessage(`${site === "ao3" ? "AO3" : "FF.net"} crawler re-enabled.`)
    } catch (e: any) { setMessage(e?.message ?? "Could not re-enable crawler.") }
  }

  if (!values) return <section className="cc-card">
    {message ? <p className="cc-msg cc-msg--bad" role="alert">{message}</p>
      : <p className="cc-dim">Loading site configuration…</p>}
  </section>

  const row = (title: string, hint: string, control: React.ReactNode) => (
    <div className="setting-row"><div className="setting-row__label">
      <span className="setting-row__name">{title}</span><span className="setting-row__hint">{hint}</span>
    </div>{control}</div>
  )
  return <>
    <header className="cc-viewhead"><div><h1>Site configuration</h1>
      <p>Defaults and crawler settings that affect every visitor. Reader preferences stay in Settings.</p></div></header>
    {message && <p className={message.endsWith("saved.") || message.endsWith("re-enabled.") ? "cc-msg" : "cc-msg cc-msg--bad"}
      role={message.endsWith("saved.") || message.endsWith("re-enabled.") ? "status" : "alert"}>{message}</p>}
    <section className="cc-card">
      <header className="cc-card__head"><h3>Reader communication</h3><p>Share a feature launch, archive delay, or maintenance update across the site. Readers can dismiss it; changing the text shows it again.</p></header>
      {row("Public announcement", "Up to 500 characters. Leave blank to hide it.",
        <textarea aria-label="Public announcement" className="setting-input site-notice-input" maxLength={500} rows={3}
          value={values.site_notice} onChange={e => set("site_notice", e.target.value)} />)}
      <p className="cc-dim">{values.site_notice.length}/500 characters</p>
    </section>
    <section className="cc-card">
      <header className="cc-card__head"><h3>Fresh content</h3><p>New works are found by walking AO3 tag pages. Rotation keeps indexing broad while mixed mode also revisits your chosen fandoms.</p></header>
      {row("Crawl strategy", "Rotate balances freshness across the index; mixed adds your tracked fandoms; pinned only refreshes those fandoms.",
        <div className="setting-pills">{[["rotate", "Rotate"], ["mixed", "Mixed"], ["pinned", "Pinned"]].map(([id, label]) =>
          <button key={id} type="button" aria-pressed={values.crawl_mode === id}
            className={`pill ${values.crawl_mode === id ? "pill--on" : ""}`} onClick={() => set("crawl_mode", id)}>{label}</button>)}</div>)}
      {values.crawl_mode !== "rotate" && row("Tracked fandoms", "Comma-separated AO3 tag names.",
        <input aria-label="Tracked fandoms" className="setting-input" value={values.tracked_fandom} onChange={e => set("tracked_fandom", e.target.value)} placeholder="Harry Potter - J. K. Rowling" />)}
      {values.crawl_mode !== "pinned" && row("Fandoms per pass", "More fandoms per pass sweep the rotation faster and spend more archive rate limit.",
        <select aria-label="Fandoms per pass" className="setting-select" value={values.crawl_rotate_count} onChange={e => set("crawl_rotate_count", e.target.value)}>
          {["1", "2", "3", "5", "8"].map(n => <option key={n} value={n}>{n}</option>)}
        </select>)}
      {row("Pull latest works on load", "Fetch recent works when the site opens.",
        <Switch value={values.poll_on_load === "true"} label="Pull latest works on load" onChange={v => set("poll_on_load", String(v))} />)}
      {row("Live AO3 search", "Top up searches with fresh AO3 results; thin searches may pull deeper.",
        <Switch value={values.live_fetch === "true"} label="Live AO3 search" onChange={v => set("live_fetch", String(v))} />)}
    </section>
    <section className="cc-card">
      <header className="cc-card__head"><h3>Feed filters</h3><p>Applied to the auto-pulled feed. Tight filters can leave fewer fresh works.</p></header>
      {row("Minimum words", "Blank means no minimum.", <input aria-label="Minimum words" className="setting-input" type="number" min={0} step={1000} value={values.feed_min_words} onChange={e => set("feed_min_words", e.target.value)} />)}
      {row("Maximum words", "Blank means no cap.", <input aria-label="Maximum words" className="setting-input" type="number" min={0} step={1000} value={values.feed_max_words} onChange={e => set("feed_max_words", e.target.value)} />)}
      {row("Complete works only", "Skip works in progress.", <Switch value={values.feed_complete_only === "true"} label="Complete works only" onChange={v => set("feed_complete_only", String(v))} />)}
    </section>
    <section className="cc-card">
      <header className="cc-card__head"><h3>Direct crawling</h3><p>Direct AO3 crawling can work from a residential connection. FF.net is currently blocked for server requests; use one-click import for it.</p></header>
      {row("Enable direct crawling", "Takes effect on the next scheduled run.", <Switch value={values.enable_direct_crawl === "true"} label="Enable direct crawling" onChange={v => set("enable_direct_crawl", String(v))} />)}
      {(autoDisabled.ao3 || autoDisabled.ffnet) && row("Paused after repeated failures", "Fix the connection before re-enabling to avoid repeated failed requests.",
        <div className="setting-pills">{autoDisabled.ao3 && <button type="button" className="pill" onClick={() => resetBreaker("ao3")}>Re-enable AO3</button>}{autoDisabled.ffnet && <button type="button" className="pill" onClick={() => resetBreaker("ffnet")}>Re-enable FF.net</button>}</div>)}
    </section>
    <div className="settings-actions settings-actions--live">
      {dirty && <span className="settings-actions__dirty">Unsaved changes</span>}
      <button className="btn btn--primary settings-save" onClick={save} disabled={saving || !dirty}>
        {saving ? "Saving…" : "Save configuration"}
      </button>
    </div>
  </>
}
