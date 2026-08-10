"use client"
import Link from "next/link"
import ThemeToggle from "./ThemeToggle"
import { useEffect, useState } from "react"
import OfflineLink from "./OfflineLink"
import IndexStatus from "./IndexStatus"
import { useAuth } from "@/lib/auth"
import { lastSearchHref } from "@/lib/lastSearch"

// The one header every page shares.
//
// Previously only the search page had navigation; Library, Settings, Account and
// the story pages each offered a single "← Back to search" link and nothing else.
// That made the whole site hub-and-spoke: getting from your Library to Settings,
// or from a story to your Library, meant going home first. Every page now carries
// the same header, so any destination is one click from anywhere.
//
// `current` suppresses the link to the page you're already on and marks it.

export type NavKey = "search" | "library" | "settings" | "account"

function UserMenu() {
  const { user, logout, loading, syncing } = useAuth()
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      const t = e.target as HTMLElement
      if (!t.closest(".user-menu")) setOpen(false)
    }
    document.addEventListener("click", close)
    return () => document.removeEventListener("click", close)
  }, [open])
  if (loading) return null
  if (!user) return <Link href="/login" className="header__link">Sign in</Link>
  return (
    <div className="user-menu">
      <button className="user-menu__btn" onClick={() => setOpen(o => !o)}>
        <span className="user-menu__avatar">
          {user.username.slice(0, 1).toUpperCase()}
          {syncing && <span className="user-menu__sync-dot" title="Syncing…" />}
        </span>
        <span className="user-menu__name">{user.username}</span>
      </button>
      {open && (
        <div className="user-menu__dropdown">
          <p className="user-menu__hint">
            {syncing
              ? "⟳ Syncing your data…"
              : "Bookmarks, reading progress, recent searches and reader settings sync to this account."}
          </p>
          <Link href="/account" className="user-menu__link" onClick={() => setOpen(false)}>Account &amp; sync</Link>
          <OfflineLink href="/library" className="user-menu__link" onClick={() => setOpen(false)}>My library</OfflineLink>
          <button onClick={async () => { await logout(); setOpen(false) }}>Sign out</button>
        </div>
      )}
    </div>
  )
}

export default function SiteHeader(
  { current, children }: { current?: NavKey; children?: React.ReactNode },
) {
  // Resolved after mount, never during render: sessionStorage does not exist on
  // the server, so reading it inline would make the server and client disagree
  // about the href and React would discard the whole tree to fix it.
  const [searchHref, setSearchHref] = useState("/")
  useEffect(() => { setSearchHref(lastSearchHref()) }, [current])
  const backToResults = searchHref !== "/"

  const link = (key: NavKey, href: string, label: string, offline = false,
                title?: string) => {
    const cls = `header__link ${current === key ? "header__link--current" : ""}`
    if (current === key) return <span className={cls} aria-current="page">{label}</span>
    return offline
      ? <OfflineLink href={href} className={cls}>{label}</OfflineLink>
      : <Link href={href} className={cls} title={title} prefetch>{label}</Link>
  }

// Tab icons as inline SVG on one grid, at one stroke weight.
//
// These were Unicode glyphs — ⌕, ▤, ⚙ — pulled from three different blocks, so
// they arrived at three different optical weights and sizes: a hairline
// magnifier beside a solid filled block beside a gear. Nothing lines them up,
// because each is whatever the system font happens to draw, and it differs again
// on iOS. Three paths on a 24-unit grid with the same 1.7 stroke are the only
// way this row reads as one set.
//
// Inline rather than an icon font or sprite: three shapes do not justify a
// download, and this has to render with no network at all — the tab bar is how
// you reach the offline library.
const ICONS: Record<string, React.ReactNode> = {
  search: (
    <><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></>
  ),
  library: (
    <><path d="M4 5.5h6a2 2 0 0 1 2 2V19a2.5 2.5 0 0 0-2.5-2H4z" />
      <path d="M20 5.5h-6a2 2 0 0 0-2 2V19a2.5 2.5 0 0 1 2.5-2H20z" /></>
  ),
  // Sliders, not a gear. A gear reduced to 21px with a 1.7 stroke loses its
  // teeth and reads as a sun — which on this site means the theme toggle sitting
  // in the header, so the two would have said the same thing.
  settings: (
    <><path d="M5 7h9M5 12h5M5 17h11" />
      <circle cx="17" cy="7" r="2" /><circle cx="13" cy="12" r="2" />
      <circle cx="19" cy="17" r="2" /></>
  ),
}

function TabIcon({ name }: { name: string }) {
  return (
    <svg className="tabbar__icon" viewBox="0 0 24 24" aria-hidden="true"
      fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name]}
    </svg>
  )
}

  // Phone navigation is a bottom tab bar rather than more items in the header.
  // Six header items at 16px gaps overflowed a 375px screen, and putting the
  // primary destinations within thumb reach is what makes this read as an app
  // rather than a shrunken desktop page. Same routes, different placement — CSS
  // shows one or the other, so there is no duplicated navigation on any screen.
  const tab = (key: NavKey, href: string, label: string, icon: string, offline = false) => {
    const cls = `tabbar__item ${current === key ? "tabbar__item--current" : ""}`
    const inner = (
      <>
        <TabIcon name={icon} />
        <span className="tabbar__label">{label}</span>
      </>
    )
    if (current === key) {
      return <span className={cls} aria-current="page">{inner}</span>
    }
    return offline
      ? <OfflineLink href={href} className={cls}>{inner}</OfflineLink>
      : <Link href={href} className={cls} prefetch>{inner}</Link>
  }

  return (
    <>
      <header className="header">
        {/* The wordmark is the way home from everywhere — the convention people
            already expect, and it was previously not a link at all. */}
        {/* The wordmark stays a clean slate even when "Search" beside it goes
            back to your results — logo-means-home is the older convention and
            people use it to start over. */}
        <Link href="/" className="logo logo--link" aria-label="FicAtlas home"
          title={backToResults ? "Start a fresh search" : "FicAtlas home"}>
          Fic<em>Atlas</em>
        </Link>
        <nav className="header__right">
          <ThemeToggle compact />
          <span className="header__nav-links">
            {link("search", searchHref, "Search",
                  false, backToResults ? "Back to your results" : undefined)}
            {link("library", "/library", "Library", true)}
            {link("settings", "/settings", "Settings")}
          </span>
          <UserMenu />
          <IndexStatus />
          {/* Page-specific controls (e.g. the Explicit toggle on search). */}
          {children}
        </nav>
      </header>

      <nav className="tabbar" aria-label="Primary">
        {tab("search", searchHref, "Search", "search")}
        {tab("library", "/library", "Library", "library", true)}
        {tab("settings", "/settings", "Settings", "settings")}
      </nav>
    </>
  )
}
