"use client"
import ThemeToggle from "../ThemeToggle"
import { useEffect, useState } from "react"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import { useAuth } from "@/lib/auth"
import { writePref, mergePrefs, type Prefs } from "@/lib/prefs"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

// Settings, split by who a setting belongs to rather than by topic.
//
// It used to be one list. Every visitor saw "Tracked fandom", "Live AO3 fetch",
// feed word-count filters and a direct-crawler switch — instance-wide operator
// controls, on a page with a single Save button. Three things were wrong with
// that, and they get worse the moment the site is public:
//
//   * /api/settings POST already requires admin, and saveAll never checked the
//     response, so a signed-out reader pressed Save and got "✓ Saved" while
//     thirteen requests 403'd. The page lied about the one thing it does.
//   * Font and width were instance-wide, so one reader choosing sans-serif
//     would have changed the default for everybody.
//   * A reader scrolled past crawler scheduling and Cloudflare notes to reach
//     the two settings that were theirs.
//
// Now: YOUR side is per-device and needs no account, saving as you touch it.
// THE SITE'S side only renders for someone who can actually save it — the same
// reasoning as can_import in the header, where showing a control that 403s
// teaches a reader the app is broken rather than that it is not theirs.
interface AdminSettings {
  tracked_fandom: string
  poll_on_load: string
  live_fetch: string
  feed_min_words: string
  feed_max_words: string
  feed_complete_only: string
  enable_direct_crawl: string
}

const ADMIN_KEYS: (keyof AdminSettings)[] = [
  "tracked_fandom", "poll_on_load", "live_fetch",
  "feed_min_words", "feed_max_words", "feed_complete_only", "enable_direct_crawl",
]

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

const PREF_FALLBACK: Prefs = {
  default_sites: "ao3,ffnet,fictionalley",
  default_sort: "relevance",
  results_per_page: "20",
  show_explicit: "false",
  reader_font: "serif",
  reader_width: "narrow",
}

export default function SettingsPage() {
  const { user, loading: authLoading } = useAuth()
  const isAdmin = !!user?.can_manage

  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [admin, setAdmin] = useState<AdminSettings | null>(null)
  // What the server last confirmed, so "has anything changed" is a comparison
  // rather than a flag that has to be cleared correctly on every path.
  const [adminSaved, setAdminSaved] = useState<AdminSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveState, setSaveState] = useState<null | { ok: boolean; text: string }>(null)
  const [autoDisabled, setAutoDisabled] = useState<{ ao3: boolean; ffnet: boolean }>({ ao3: false, ffnet: false })
  const [pendingTakedowns, setPendingTakedowns] = useState(0)

  const loadCrawlStatus = () =>
    fetch(`${API_BASE}/api/library/crawl-status`).then(r => r.json())
      .then(d => setAutoDisabled(d.auto_disabled || { ao3: false, ffnet: false })).catch(() => {})

  useEffect(() => {
    // The server row is the INSTANCE default; this device's own choices win over
    // it. Same read order the reader has always used for font and width.
    fetch(`${API_BASE}/api/settings`).then(r => r.json()).then(s => {
      setPrefs({ ...PREF_FALLBACK, ...s, ...mergePrefs({}) } as Prefs)
      const loaded = Object.fromEntries(ADMIN_KEYS.map(k => [k, s[k] ?? ""])) as AdminSettings
      setAdmin(loaded); setAdminSaved(loaded)
    }).catch(() => {
      // Offline or the API is down — your own preferences are on this device
      // and still perfectly editable, so show them rather than a dead page.
      setPrefs({ ...PREF_FALLBACK, ...mergePrefs({}) } as Prefs)
    })
    loadCrawlStatus()
  }, [])

  useEffect(() => {
    if (!isAdmin) return
    fetch(`${API_BASE}/api/takedown/pending-count`, { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setPendingTakedowns(d.pending || 0))
      .catch(() => {})
  }, [isAdmin])

  // Preferences save the moment you touch them. There is no Save button for
  // this half and no failure mode worth reporting: it is a localStorage write
  // on the device you are holding.
  const setPref = (key: keyof Prefs, value: string) => {
    setPrefs(p => p ? { ...p, [key]: value } : p)
    writePref(key, value)
  }

  const setAdminValue = (key: keyof AdminSettings, value: string) =>
    setAdmin(a => a ? { ...a, [key]: value } : a)

  const resetBreaker = async (site: "ao3" | "ffnet") => {
    try {
      const fd = new FormData(); fd.append("site", site)
      await fetch(`${API_BASE}/api/library/crawl-reset-breaker`, { method: "POST", body: fd })
      loadCrawlStatus()
    } catch {}
  }

  const saveAdmin = async () => {
    if (!admin) return
    setSaving(true); setSaveState(null)
    try {
      // Checked, unlike before. Anything that did not save has to say so —
      // "✓ Saved" over a 403 is worse than no feedback at all, because you go
      // away believing the crawler is configured.
      const results = await Promise.all(ADMIN_KEYS.map(async key => {
        const fd = new FormData()
        fd.append("key", key); fd.append("value", String(admin[key] ?? ""))
        try {
          const r = await fetch(`${API_BASE}/api/settings`, { method: "POST", body: fd })
          return r.ok
        } catch { return false }
      }))
      const failed = results.filter(x => !x).length
      if (failed === 0) {
        setAdminSaved(admin)          // this is now the confirmed state
        setSaveState({ ok: true, text: "✓ Saved" })
        setTimeout(() => setSaveState(null), 2500)
      } else {
        setSaveState({ ok: false, text: `${failed} of ${results.length} settings could not be saved — you may have been signed out.` })
      }
    } finally {
      setSaving(false)
    }
  }

  const toggleSite = (id: string) => {
    if (!prefs) return
    const cur = prefs.default_sites.split(",").filter(Boolean)
    // Never let the last one go: an empty site list searches nothing, and the
    // control gives no hint that is what just happened.
    const next = cur.includes(id)
      ? (cur.length > 1 ? cur.filter(x => x !== id) : cur)
      : [...cur, id]
    setPref("default_sites", next.join(","))
  }

  if (!prefs) return (
    <div className="settings-shell">
      <SiteHeader current="settings" />
      <p className="loading">Loading…</p>
    </div>
  )

  const sites = prefs.default_sites.split(",").filter(Boolean)
  // The admin half is ~1500px tall, so a save button pinned at its end sits
  // off-screen while you are editing the field it saves. position:sticky could
  // not fix that — as the last child it has no scroll range to float within, so
  // it simply arrived at the bottom like any other element. A bar that appears
  // only once something has actually changed is better than either: it stays
  // reachable when it matters and is absent the rest of the time.
  const dirty = !!admin && !!adminSaved &&
    ADMIN_KEYS.some(k => (admin[k] ?? "") !== (adminSaved[k] ?? ""))

  return (
    <div className="settings-shell">
      <SiteHeader current="settings" />
      <h1 className="settings-title">Settings</h1>

      <p className="settings-lede">
        These are yours and live on this device — no account needed, and nothing
        here changes what anybody else sees.
      </p>

      <section className="settings-group">
        <h2 className="settings-group__title">Appearance</h2>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Theme</span>
            <span className="setting-row__hint">
              Light, dark, or whatever your device is set to.
            </span>
          </div>
          <ThemeToggle />
        </div>
      </section>

      <section className="settings-group">
        <h2 className="settings-group__title">Reading</h2>
        <p className="settings-group__hint">
          How a story looks when you open it. You can also change these from
          inside the reader, and the two stay in step.
        </p>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Font</span>
            <span className="setting-row__hint">Serif for long stretches, sans for screens.</span>
          </div>
          <div className="setting-pills">
            {[["serif", "Serif"], ["sans", "Sans"]].map(([id, label]) => (
              <button key={id} aria-pressed={prefs.reader_font === id}
                className={`pill ${prefs.reader_font === id ? "pill--on" : ""}`}
                onClick={() => setPref("reader_font", id)}>{label}</button>
            ))}
          </div>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Line width</span>
            <span className="setting-row__hint">Narrow keeps lines short enough to track by eye.</span>
          </div>
          <div className="setting-pills">
            {[["narrow", "Narrow"], ["wide", "Wide"]].map(([id, label]) => (
              <button key={id} aria-pressed={prefs.reader_width === id}
                className={`pill ${prefs.reader_width === id ? "pill--on" : ""}`}
                onClick={() => setPref("reader_width", id)}>{label}</button>
            ))}
          </div>
        </div>
      </section>

      <section className="settings-group">
        <h2 className="settings-group__title">Search</h2>
        <p className="settings-group__hint">
          Where a fresh search starts. Changing filters on the search page itself
          does not change these.
        </p>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Archives</span>
            <span className="setting-row__hint">Which archives to search by default.</span>
          </div>
          <div className="setting-pills">
            {SITE_OPTIONS.map(o => (
              <button key={o.id} onClick={() => toggleSite(o.id)}
                aria-pressed={sites.includes(o.id)}
                title={sites.length === 1 && sites.includes(o.id)
                  ? "At least one archive has to stay selected" : undefined}
                className={`pill ${sites.includes(o.id) ? "pill--on" : ""}`}>{o.label}</button>
            ))}
          </div>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Sort by</span>
          </div>
          <select className="setting-select" value={prefs.default_sort}
            onChange={e => setPref("default_sort", e.target.value)}>
            {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Results per page</span>
          </div>
          <select className="setting-select" value={prefs.results_per_page}
            onChange={e => setPref("results_per_page", e.target.value)}>
            {["10", "20", "30", "50"].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Show explicit works</span>
            <span className="setting-row__hint">
              Include E-rated works in results without switching it on each time.
            </span>
          </div>
          <Toggle on={prefs.show_explicit === "true"}
            label="Show explicit works"
            onToggle={v => setPref("show_explicit", String(v))} />
        </div>
      </section>

      {/* ── the site's own settings ─────────────────────────────────────────
          Below the fold and visibly separate, because these are not about the
          person reading — they change what the instance indexes, for everyone. */}
      {isAdmin && admin && (
        <>
          <div className="settings-divider">
            <span className="settings-divider__label">Site administration</span>
          </div>
          <p className="settings-lede settings-lede--admin">
            These apply to the whole instance and to every visitor. Readers never
            see this section.
          </p>

          {/* First, because it is the only part of the admin surface where a
              person is waiting for an answer. Everything below it is a
              preference that can sit unchanged for months. */}
          <section className="settings-group">
            <h2 className="settings-group__title">Requests from authors</h2>
            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">
                  Takedown requests
                  {pendingTakedowns > 0 && (
                    <span className="settings-badge">{pendingTakedowns} waiting</span>
                  )}
                </span>
                <span className="setting-row__hint">
                  {pendingTakedowns > 0
                    ? "The text is already down — what is outstanding is your decision, and replying to the person who asked."
                    : "Nothing waiting. Text comes down automatically when a request arrives; this is where you decide whether it stays down."}
                </span>
              </div>
              <Link href="/takedowns" className="btn btn--ghost">Open queue</Link>
            </div>
          </section>

          <section className="settings-group">
            <h2 className="settings-group__title">Fresh content</h2>

            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Tracked fandom</span>
                <span className="setting-row__hint">Auto-pulled from AO3 feeds on load and every 6h. Comma-separate for multiple.</span>
              </div>
              <input className="setting-input" value={admin.tracked_fandom}
                onChange={e => setAdminValue("tracked_fandom", e.target.value)}
                placeholder="Harry Potter - J. K. Rowling" />
            </div>

            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Pull on load</span>
                <span className="setting-row__hint">Fetch latest works each time the site opens.</span>
              </div>
              <Toggle on={admin.poll_on_load === "true"} label="Pull on load"
                onToggle={v => setAdminValue("poll_on_load", String(v))} />
            </div>

            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Live AO3 fetch</span>
                <span className="setting-row__hint">Top up each search with fresh AO3 results. Thin searches also auto-pull deeper from AO3.</span>
              </div>
              <Toggle on={admin.live_fetch === "true"} label="Live AO3 fetch"
                onToggle={v => setAdminValue("live_fetch", String(v))} />
            </div>
          </section>

          <section className="settings-group">
            <h2 className="settings-group__title">Feed filters</h2>
            <p className="settings-group__hint">
              Applied to the auto-pulled feed. The feed always returns the 25 most recent works
              for a tag; only the ones matching these are kept, so tight filters yield fewer results.
            </p>

            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Minimum word count</span>
                <span className="setting-row__hint">e.g. 100000 to keep only works over 100k. Blank for none.</span>
              </div>
              <input className="setting-input" type="number" min={0} step={1000}
                value={admin.feed_min_words}
                onChange={e => setAdminValue("feed_min_words", e.target.value)}
                placeholder="(no minimum)" />
            </div>

            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Maximum word count</span>
                <span className="setting-row__hint">Blank for no cap.</span>
              </div>
              <input className="setting-input" type="number" min={0} step={1000}
                value={admin.feed_max_words}
                onChange={e => setAdminValue("feed_max_words", e.target.value)}
                placeholder="(no maximum)" />
            </div>

            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Complete only</span>
                <span className="setting-row__hint">Skip works in progress.</span>
              </div>
              <Toggle on={admin.feed_complete_only === "true"} label="Complete only"
                onToggle={v => setAdminValue("feed_complete_only", String(v))} />
            </div>
          </section>

          <section className="settings-group">
            <h2 className="settings-group__title">Direct crawling</h2>
            <p className="settings-group__hint">
              When on, FicAtlas crawls AO3 and FF.net directly every few hours, on top of the
              Atom feed poller. AO3 works from a residential connection — its heavy pages are
              just slow (5–20s), which the app waits out. <strong>FF.net stays
              Cloudflare-blocked</strong> for direct server requests whatever the IP, so its
              crawler will keep failing; use one-click URL import (via FicHub) for FF.net
              instead. Leave off if you rely on feeds and imports.
            </p>
            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">Enable direct AO3 / FF.net crawling</span>
                <span className="setting-row__hint">Takes effect on the next scheduled run — no restart. Check backend logs to confirm it is reaching the sites.</span>
              </div>
              <Toggle on={admin.enable_direct_crawl === "true"} label="Direct crawling"
                onToggle={v => setAdminValue("enable_direct_crawl", String(v))} />
            </div>
            {(autoDisabled.ao3 || autoDisabled.ffnet) && (
              <div className="setting-row">
                <div className="setting-row__label">
                  <span className="setting-row__name">⚠ Auto-disabled crawlers</span>
                  <span className="setting-row__hint">
                    A site is paused automatically after repeated crawl failures so it stops
                    hammering a blocked endpoint and filling the log. Fix connectivity, then
                    re-enable here — it resumes on the next scheduled run.
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

          <div className={`settings-actions ${dirty || saving || saveState ? "settings-actions--live" : ""}`}>
            {saveState && !saveState.ok && (
              <p className="settings-save-error" role="alert">{saveState.text}</p>
            )}
            {dirty && !saving && (
              <span className="settings-actions__dirty">Unsaved changes</span>
            )}
            <button className="btn btn--primary settings-save" onClick={saveAdmin}
              disabled={saving || (!dirty && !saveState)}>
              {saving ? "Saving…" : saveState?.ok ? "✓ Saved" : "Save site settings"}
            </button>
          </div>
        </>
      )}

      {/* Signed out, nothing above needed an account — so this is an offer,
          not a gate. Says what signing in adds rather than what it unlocks. */}
      {!authLoading && !user && (
        <p className="settings-footnote">
          <Link href="/login" className="library-signin-note__link">Sign in</Link>{" "}
          to sync bookmarks and reading progress across your devices. Everything
          on this page works without one.
        </p>
      )}
    </div>
  )
}

function Toggle({ on, onToggle, label }: {
  on: boolean; onToggle: (v: boolean) => void; label?: string
}) {
  return (
    <button className={`switch ${on ? "switch--on" : ""}`} onClick={() => onToggle(!on)}
      role="switch" aria-checked={on} aria-label={label}>
      <span className="switch__thumb" />
    </button>
  )
}
