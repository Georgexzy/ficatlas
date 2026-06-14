"use client"
import { useEffect, useState } from "react"
import Link from "next/link"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (typeof window !== "undefined" ? `${window.location.protocol}//${window.location.hostname}:8000` : "http://localhost:8000")

interface Settings {
  tracked_fandom: string
  poll_on_load: string
  default_sites: string
  default_sort: string
  results_per_page: string
  reader_font: string
  reader_width: string
  show_explicit: string
  live_fetch: string
  feed_min_words: string
  feed_max_words: string
  feed_complete_only: string
}

const SITE_OPTIONS = [
  { id: "ao3", label: "AO3" },
  { id: "ffnet", label: "FF.net" },
  { id: "fictionalley", label: "FicAlley" },
]
const SORT_OPTIONS = [
  { value: "relevance", label: "Relevance" },
  { value: "updated_desc", label: "Recently updated" },
  { value: "published_desc", label: "Recently published" },
  { value: "kudos_desc", label: "Most kudos" },
  { value: "word_count_desc", label: "Longest" },
  { value: "word_count_asc", label: "Shortest" },
]

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`).then(r => r.json()).then(setSettings).catch(() => {})
  }, [])

  const update = (key: keyof Settings, value: string) =>
    setSettings(s => s ? { ...s, [key]: value } : s)

  const saveAll = async () => {
    if (!settings) return
    setSaving(true); setSaved(false)
    try {
      await Promise.all(Object.entries(settings).map(([key, value]) => {
        const fd = new FormData()
        fd.append("key", key); fd.append("value", String(value))
        return fetch(`${API_BASE}/api/settings`, { method: "POST", body: fd })
      }))
      // Mirror a couple to localStorage so the search page can read instantly
      localStorage.setItem("ficatlas:default_sites", settings.default_sites)
      localStorage.setItem("ficatlas:reader_font", settings.reader_font)
      localStorage.setItem("ficatlas:reader_width", settings.reader_width)
      setSaved(true); setTimeout(() => setSaved(false), 2500)
    } finally {
      setSaving(false)
    }
  }

  const toggleSite = (id: string) => {
    if (!settings) return
    const cur = settings.default_sites.split(",").filter(Boolean)
    const next = cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id]
    update("default_sites", next.join(","))
  }

  if (!settings) return (
    <div className="settings-shell">
      <Link href="/" className="back-link">← Back to search</Link>
      <p className="loading">Loading…</p>
    </div>
  )

  const sites = settings.default_sites.split(",").filter(Boolean)

  return (
    <div className="settings-shell">
      <Link href="/" className="back-link">← Back to search</Link>
      <h1 className="settings-title">Settings</h1>

      <section className="settings-group">
        <h2 className="settings-group__title">Fresh content</h2>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Tracked fandom</span>
            <span className="setting-row__hint">Auto-pulled from AO3 feeds on load and every 6h. Comma-separate for multiple.</span>
          </div>
          <input className="setting-input" value={settings.tracked_fandom}
            onChange={e => update("tracked_fandom", e.target.value)}
            placeholder="Harry Potter - J. K. Rowling" />
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Pull on load</span>
            <span className="setting-row__hint">Fetch latest works each time the site opens.</span>
          </div>
          <Toggle on={settings.poll_on_load === "true"} onToggle={v => update("poll_on_load", String(v))} />
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Live AO3 fetch</span>
            <span className="setting-row__hint">Pull fresh AO3 results on each search (slower but current).</span>
          </div>
          <Toggle on={settings.live_fetch === "true"} onToggle={v => update("live_fetch", String(v))} />
        </div>
      </section>

      <section className="settings-group">
        <h2 className="settings-group__title">Feed filters</h2>
        <p className="settings-group__hint">
          Filters apply to the auto-pulled feed. The feed always returns the 25 most recent works
          for a tag; we keep only the ones matching these criteria, so tight filters yield fewer results.
        </p>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Minimum word count</span>
            <span className="setting-row__hint">e.g. 100000 to keep only works over 100k. Blank for none.</span>
          </div>
          <input className="setting-input" type="number" min={0} step={1000}
            value={settings.feed_min_words}
            onChange={e => update("feed_min_words", e.target.value)}
            placeholder="(no minimum)" />
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Maximum word count</span>
            <span className="setting-row__hint">Blank for no cap.</span>
          </div>
          <input className="setting-input" type="number" min={0} step={1000}
            value={settings.feed_max_words}
            onChange={e => update("feed_max_words", e.target.value)}
            placeholder="(no maximum)" />
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Complete only</span>
            <span className="setting-row__hint">Skip works in progress.</span>
          </div>
          <Toggle on={settings.feed_complete_only === "true"} onToggle={v => update("feed_complete_only", String(v))} />
        </div>
      </section>

      <section className="settings-group">
        <h2 className="settings-group__title">Search defaults</h2>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Default sites</span>
            <span className="setting-row__hint">Which archives to search by default.</span>
          </div>
          <div className="setting-pills">
            {SITE_OPTIONS.map(o => (
              <button key={o.id} onClick={() => toggleSite(o.id)}
                className={`pill ${sites.includes(o.id) ? "pill--on" : ""}`}>{o.label}</button>
            ))}
          </div>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Default sort</span>
          </div>
          <select className="setting-select" value={settings.default_sort}
            onChange={e => update("default_sort", e.target.value)}>
            {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Results per page</span>
          </div>
          <select className="setting-select" value={settings.results_per_page}
            onChange={e => update("results_per_page", e.target.value)}>
            {["10", "20", "30", "50"].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Show explicit by default</span>
          </div>
          <Toggle on={settings.show_explicit === "true"} onToggle={v => update("show_explicit", String(v))} />
        </div>
      </section>

      <section className="settings-group">
        <h2 className="settings-group__title">Reader</h2>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Font</span>
          </div>
          <div className="setting-pills">
            <button className={`pill ${settings.reader_font === "serif" ? "pill--on" : ""}`}
              onClick={() => update("reader_font", "serif")}>Serif</button>
            <button className={`pill ${settings.reader_font === "sans" ? "pill--on" : ""}`}
              onClick={() => update("reader_font", "sans")}>Sans</button>
          </div>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Width</span>
          </div>
          <div className="setting-pills">
            <button className={`pill ${settings.reader_width === "narrow" ? "pill--on" : ""}`}
              onClick={() => update("reader_width", "narrow")}>Narrow</button>
            <button className={`pill ${settings.reader_width === "wide" ? "pill--on" : ""}`}
              onClick={() => update("reader_width", "wide")}>Wide</button>
          </div>
        </div>
      </section>

      <div className="settings-actions">
        <button className="btn btn--primary settings-save" onClick={saveAll} disabled={saving}>
          {saving ? "Saving…" : saved ? "✓ Saved" : "Save settings"}
        </button>
      </div>
    </div>
  )
}

function Toggle({ on, onToggle }: { on: boolean; onToggle: (v: boolean) => void }) {
  return (
    <button className={`switch ${on ? "switch--on" : ""}`} onClick={() => onToggle(!on)} role="switch" aria-checked={on}>
      <span className="switch__thumb" />
    </button>
  )
}
