"use client"
import { useEffect, useState } from "react"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import { useAuth } from "@/lib/auth"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

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
  enable_direct_crawl: string
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
  const { user, loading: authLoading } = useAuth()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [autoDisabled, setAutoDisabled] = useState<{ ao3: boolean; ffnet: boolean }>({ ao3: false, ffnet: false })

  const loadCrawlStatus = () =>
    fetch(`${API_BASE}/api/library/crawl-status`).then(r => r.json())
      .then(d => setAutoDisabled(d.auto_disabled || { ao3: false, ffnet: false })).catch(() => {})

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`).then(r => r.json()).then(setSettings).catch(() => {})
    loadCrawlStatus()
  }, [])

  const resetBreaker = async (site: "ao3" | "ffnet") => {
    try {
      const fd = new FormData(); fd.append("site", site)
      await fetch(`${API_BASE}/api/library/crawl-reset-breaker`, { method: "POST", body: fd })
      loadCrawlStatus()
    } catch {}
  }

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
      <SiteHeader current="settings" />
      <p className="loading">Loading…</p>
    </div>
  )

  const sites = settings.default_sites.split(",").filter(Boolean)

  return (
    <div className="settings-shell">
      <SiteHeader current="settings" />
      <h1 className="settings-title">Settings</h1>

      {/* These are instance-wide settings, not per-user, so saving them needs an
          account — same guard as the library admin actions. */}
      {!authLoading && !user && (
        <p className="library-signin-note">
          <Link href="/login" className="library-signin-note__link">Sign in</Link>{" "}
          to change these — they apply to the whole instance.
        </p>
      )}

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
            <span className="setting-row__hint">Top up each search with fresh AO3 results. Thin searches also auto-pull deeper from AO3.</span>
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
        <h2 className="settings-group__title">Advanced: direct crawling</h2>
        <p className="settings-group__hint">
          When on, FicAtlas runs scheduled background crawls of AO3 and FF.net directly
          (every few hours) in addition to the Atom feed poller. AO3 works from a normal
          home/residential connection — its heavy pages are just slow (5–20s), which the
          app now waits out. <strong>FF.net remains Cloudflare-blocked</strong> for direct
          server requests regardless of IP, so its crawler will keep failing; use one-click
          URL import (via FicHub) for FF.net instead. Leave this off if you mainly rely on
          feeds and imports.
        </p>
        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Enable direct AO3 / FF.net crawling</span>
            <span className="setting-row__hint">Takes effect on the next scheduled run (no restart needed). Check backend logs to confirm it's reaching the sites.</span>
          </div>
          <Toggle on={settings.enable_direct_crawl === "true"} onToggle={v => update("enable_direct_crawl", String(v))} />
        </div>
        {(autoDisabled.ao3 || autoDisabled.ffnet) && (
          <div className="setting-row">
            <div className="setting-row__label">
              <span className="setting-row__name">⚠ Auto-disabled crawlers</span>
              <span className="setting-row__hint">
                A site is paused automatically after repeated crawl failures so it stops
                hammering a blocked endpoint and filling the log. Fix connectivity, then
                re-enable it here — it&apos;ll resume on the next scheduled run.
              </span>
            </div>
            <div className="setting-pills">
              {autoDisabled.ao3 && (
                <button className="pill" onClick={() => resetBreaker("ao3")}>Re-enable AO3</button>
              )}
              {autoDisabled.ffnet && (
                <button className="pill" onClick={() => resetBreaker("ffnet")}>Re-enable FF.net</button>
              )}
            </div>
          </div>
        )}
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
