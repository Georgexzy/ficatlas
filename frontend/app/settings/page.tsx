"use client"

import ThemeToggle from "../ThemeToggle"
import { useEffect, useState } from "react"
import Link from "next/link"
import BackLink from "../BackLink"
import SiteHeader from "../SiteHeader"
import { useAuth } from "@/lib/auth"
import { writePref, mergePrefs, type Prefs } from "@/lib/prefs"
import { fetchJson } from "@/lib/errors"
import { EMPTY_MUTES, loadMutes, muteCount, saveMutes, type MuteList } from "@/lib/mutelist"
import { DATA_GROUPS, clearGroup, downloadExport, groupSize } from "@/lib/localdata"
import { fmtBytes, storageEstimate, type StorageEstimate } from "@/lib/offline"

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
  crawl_mode: string
  crawl_rotate_count: string
  poll_on_load: string
  live_fetch: string
  feed_min_words: string
  feed_max_words: string
  feed_complete_only: string
  enable_direct_crawl: string
}

const ADMIN_KEYS: (keyof AdminSettings)[] = [
  "tracked_fandom", "crawl_mode", "crawl_rotate_count", "poll_on_load", "live_fetch",
  "feed_min_words", "feed_max_words", "feed_complete_only", "enable_direct_crawl",
]

// The five things worth muting, in the order people reach for them. Ships and
// tags lead because those are what a reader wants gone; authors last because
// muting a person is a heavier act than muting a trope, and burying it slightly
// is the right default.
const MUTE_FIELDS: { key: keyof MuteList; name: string; hint: string; placeholder: string }[] = [
  { key: "relationships", name: "Ships", placeholder: "e.g. Draco Malfoy/Harry Potter",
    hint: "Relationship tags. Hidden wherever they appear." },
  { key: "tags", name: "Tags", placeholder: "e.g. Character Death",
    hint: "Any freeform or additional tag." },
  { key: "fandoms", name: "Fandoms", placeholder: "e.g. Original Work",
    hint: "Whole fandoms you never want in results." },
  { key: "characters", name: "Characters", placeholder: "e.g. Umbridge",
    hint: "Character tags." },
  { key: "authors", name: "Authors", placeholder: "pen name, exactly as written",
    hint: "Matched on the whole pen name, ignoring capitals." },
]

const SITE_OPTIONS = [
  { id: "ao3", label: "AO3" },
  { id: "ffnet", label: "FF.net" },
  { id: "fictionalley", label: "FictionAlley" },
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

  // The standing mute list. Saved on every edit, like the rest of this side of
  // the page — there is no Save button for a reader's own settings.
  const [mutes, setMutes] = useState<MuteList>(EMPTY_MUTES)
  const [muteDrafts, setMuteDrafts] = useState<Record<string, string>>({})
  useEffect(() => { setMutes(loadMutes()) }, [])
  const muteTotal = muteCount(mutes)

  const addMute = (key: keyof MuteList) => {
    const value = (muteDrafts[key] ?? "").trim()
    if (!value) return
    // Case-insensitive: adding "Drarry" when "drarry" is already hidden should
    // not produce two entries that both have to be removed later.
    if (mutes[key].some(v => v.toLowerCase() === value.toLowerCase())) {
      setMuteDrafts(d => ({ ...d, [key]: "" }))
      return
    }
    const next = { ...mutes, [key]: [...mutes[key], value] }
    setMutes(next); saveMutes(next)
    setMuteDrafts(d => ({ ...d, [key]: "" }))
  }

  // How much of each kind of data is actually here, so "Clear" is never offered
  // for something that is already empty.
  const [dataSizes, setDataSizes] = useState<Record<string, number>>({})
  const [storage, setStorage] = useState<StorageEstimate | null>(null)
  const refreshSizes = () =>
    setDataSizes(Object.fromEntries(DATA_GROUPS.map(g => [g.id, groupSize(g)])))
  useEffect(() => {
    refreshSizes()
    storageEstimate().then(setStorage).catch(() => {})
  }, [])

  const removeMute = (key: keyof MuteList, value: string) => {
    const next = { ...mutes, [key]: mutes[key].filter(v => v !== value) }
    setMutes(next); saveMutes(next)
  }

  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [admin, setAdmin] = useState<AdminSettings | null>(null)
  // What the server last confirmed, so "has anything changed" is a comparison
  // rather than a flag that has to be cleared correctly on every path.
  const [adminSaved, setAdminSaved] = useState<AdminSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveState, setSaveState] = useState<null | { ok: boolean; text: string }>(null)
  const [autoDisabled, setAutoDisabled] = useState<{ ao3: boolean; ffnet: boolean }>({ ao3: false, ffnet: false })
  const [pendingTakedowns, setPendingTakedowns] = useState(0)

  // These three go through fetchJson purely for its timeout. Their failure
  // handling is already right — a settings page whose server call fails is still
  // a usable settings page, because the preferences that matter to a reader live
  // on this device — but a raw fetch cannot fail, it can only hang, and a
  // settings screen stuck behind a spinner is not usable at all.
  const loadCrawlStatus = () =>
    fetchJson(`${API_BASE}/api/library/crawl-status`)
      .then(d => setAutoDisabled(d.auto_disabled || { ao3: false, ffnet: false }))
      .catch(() => {})

  useEffect(() => {
    // The server row is the INSTANCE default; this device's own choices win over
    // it. Same read order the reader has always used for font and width.
    fetchJson(`${API_BASE}/api/settings`).then(s => {
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
    fetchJson(`${API_BASE}/api/takedown/pending-count`, { credentials: "include" })
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
      <BackLink fallback="/" fallbackLabel="Back to search" />
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
      <BackLink fallback="/" fallbackLabel="Back to search" />
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
          <select className="setting-select" aria-label="Default sort order"
            value={prefs.default_sort}
            onChange={e => setPref("default_sort", e.target.value)}>
            {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Results per page</span>
          </div>
          <select className="setting-select" aria-label="Results per page"
            value={prefs.results_per_page}
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

      {/* ── Never show me ───────────────────────────────────────────────────
          Exclusions have always existed, but only for the search you are
          running: type them, get results, and they are gone next time. What a
          reader actually wants from an index this size is standing — a ship
          they will never read, a trope they bounce off, an author they would
          rather not see — applied to every search without being retyped.

          Per device and never sent anywhere but the search itself. A list of
          things someone refuses to read is revealing in a way a search query is
          not, and the safest place for it is a machine we cannot see. */}
      <section className="settings-group">
        <h2 className="settings-group__title">Never show me</h2>
        <p className="settings-group__hint">
          Anything listed here is filtered out of every search, automatically.
          Kept on this device only — it is never uploaded, and it is not included
          when you share a search link.
          {muteTotal > 0 && <> Currently hiding <strong>{muteTotal}</strong>{" "}
            {muteTotal === 1 ? "thing" : "things"}.</>}
        </p>

        {MUTE_FIELDS.map(f => (
          <div className="setting-row setting-row--stack" key={f.key}>
            <div className="setting-row__label">
              <span className="setting-row__name">{f.name}</span>
              <span className="setting-row__hint">{f.hint}</span>
            </div>
            <div className="mute-field">
              <form onSubmit={e => { e.preventDefault(); addMute(f.key) }}>
                <input className="setting-input" placeholder={f.placeholder}
                  value={muteDrafts[f.key] ?? ""}
                  onChange={e => setMuteDrafts(d => ({ ...d, [f.key]: e.target.value }))} />
              </form>
              {mutes[f.key].length > 0 && (
                <div className="tag-input__chips">
                  {mutes[f.key].map(v => (
                    <button key={v} className="chip" title="Stop hiding this"
                      onClick={() => removeMute(f.key, v)}>{v} ✕</button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </section>

      {/* ── Your data ───────────────────────────────────────────────────────
          This site is account-optional: progress, bookmarks, searches, reader
          preferences and the mute list all live on this device, and saved works
          live in IndexedDB. Good for privacy, but nineteen keys had accumulated
          with no page that admitted they existed, no way to clear any one of
          them, and no way to take them with you.

          "It never leaves your device" is only half a promise. The other half is
          being able to see it and delete it. */}
      <section className="settings-group">
        <h2 className="settings-group__title">Your data</h2>
        <p className="settings-group__hint">
          Everything below is stored on this device and never uploaded. Clearing
          your browser data removes it too.
        </p>

        {DATA_GROUPS.map(g => {
          const size = dataSizes[g.id] ?? 0
          return (
            <div className="setting-row" key={g.id}>
              <div className="setting-row__label">
                <span className="setting-row__name">{g.name}</span>
                <span className="setting-row__hint">{g.hint}</span>
              </div>
              <div className="data-row__actions">
                <span className="data-row__size">
                  {size ? fmtBytes(size) : "empty"}
                </span>
                <button className="btn btn--ghost btn--sm" disabled={!size}
                  onClick={() => {
                    // Confirmed because these are not recoverable and one of
                    // them is your place in every story you are reading.
                    if (!confirm(`Clear ${g.name.toLowerCase()}? This cannot be undone.`)) return
                    clearGroup(g); refreshSizes()
                    if (g.id === "mutes") setMutes(EMPTY_MUTES)
                  }}>
                  Clear
                </button>
              </div>
            </div>
          )
        })}

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Saved for offline</span>
            <span className="setting-row__hint">
              Full text of works you downloaded, stored separately.
            </span>
          </div>
          <div className="data-row__actions">
            <span className="data-row__size">
              {storage ? `${fmtBytes(storage.usage)} used` : "—"}
            </span>
            {/* Managing them individually already exists on the shelf, and a
                second list here would be a second thing to keep in step. */}
            <Link href="/library" className="btn btn--ghost btn--sm">Manage</Link>
          </div>
        </div>

        <div className="setting-row">
          <div className="setting-row__label">
            <span className="setting-row__name">Export everything</span>
            <span className="setting-row__hint">
              A JSON file of everything above, so you can read it yourself rather
              than take our word for what is here.
            </span>
          </div>
          <button className="btn btn--ghost btn--sm" onClick={downloadExport}>
            Download
          </button>
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

          {/* One entry, because there is now one page.
              These were two sections — "Requests from authors" and "Index
              health" — pointing into what became a single tabbed admin page when
              those two routes were merged, so Settings still presented the
              takedown queue as a destination of its own alongside the page that
              contains it. The pending count stays: it is the only part of the
              admin surface where a person is waiting on an answer, and it is
              worth seeing without opening anything. */}
          <section className="settings-group">
            <h2 className="settings-group__title">Operator tools</h2>
            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">
                  Index health and author requests
                  {pendingTakedowns > 0 && (
                    <span className="settings-badge">{pendingTakedowns} waiting</span>
                  )}
                </span>
                <span className="setting-row__hint">
                  {pendingTakedowns > 0
                    ? "Someone is waiting on a decision — the text is already down, but the reply is not sent. Also: what is thin, what the crawler is pointed at next, and whether AO3 is throttling us."
                    : "What is thin, what the crawler is pointed at next, whether AO3 is throttling us, and any takedown requests — previously only visible by reading container logs."}
                </span>
              </div>
              <Link href={pendingTakedowns > 0 ? "/admin?tab=takedowns" : "/admin"}
                className="btn btn--ghost">Open</Link>
            </div>
          </section>

          <section className="settings-group">
            <h2 className="settings-group__title">Fresh content</h2>

            {/* This loop is the ONLY source of works published after the bulk
                dumps end, so whatever it is pointed at decides what the whole
                fresh end of the index contains. Pinned to one fandom, that
                meant everything newer than mid-2024 was Harry Potter and
                nothing else — a site-wide bias produced by one person's taste
                and invisible from the outside. Hence the mode. */}
            <div className="setting-row">
              <div className="setting-row__label">
                <span className="setting-row__name">What to keep current</span>
                <span className="setting-row__hint">
                  New works are found by walking AO3 tag pages. This is the only
                  route for anything published after the bulk imports, so it
                  decides which fandoms stay up to date.
                </span>
              </div>
              <div className="setting-pills">
                {[["rotate", "Rotate"], ["mixed", "Mixed"], ["pinned", "Pinned"]].map(([id, label]) => (
                  <button key={id} aria-pressed={admin.crawl_mode === id}
                    className={`pill ${admin.crawl_mode === id ? "pill--on" : ""}`}
                    onClick={() => setAdminValue("crawl_mode", id)}>{label}</button>
                ))}
              </div>
            </div>
            <p className="settings-group__hint settings-group__hint--tight">
              {admin.crawl_mode === "rotate"
                ? "Walks the largest fandoms in the index in turn, so coverage follows what the index actually holds rather than anyone's preferences."
                : admin.crawl_mode === "pinned"
                ? "Only the fandoms listed below. Everything else stops gaining new works — fine for a personal instance, a visible bias on a public one."
                : "Your fandoms every pass, plus the next few from the rotation. Keeps what you read current without it being the only thing that is."}
            </p>

            {admin.crawl_mode !== "rotate" && (
              <div className="setting-row">
                <div className="setting-row__label">
                  <span className="setting-row__name">Your fandoms</span>
                  <span className="setting-row__hint">Comma-separate for several. Use AO3&apos;s own tag name.</span>
                </div>
                <input className="setting-input" value={admin.tracked_fandom}
                  onChange={e => setAdminValue("tracked_fandom", e.target.value)}
                  placeholder="Harry Potter - J. K. Rowling" />
              </div>
            )}

            {admin.crawl_mode !== "pinned" && (
              <div className="setting-row">
                <div className="setting-row__label">
                  <span className="setting-row__name">Fandoms per pass</span>
                  <span className="setting-row__hint">
                    How many rotation fandoms each run visits. Higher sweeps the
                    list faster and spends more of the AO3 rate limit doing it.
                  </span>
                </div>
                <select className="setting-select" value={admin.crawl_rotate_count}
                  onChange={e => setAdminValue("crawl_rotate_count", e.target.value)}>
                  {["1", "2", "3", "5", "8"].map(n => <option key={n} value={n}>{n}</option>)}
                </select>
              </div>
            )}

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
