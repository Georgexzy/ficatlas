"use client"
import { useMemo, useRef, useState } from "react"
import Link from "next/link"

// A filterable A-Z index. The /fandoms and /ships pages list thousands of
// entries; on a phone that is an endless scroll with no way to narrow it.
// This component wraps the list with a search input that filters client-side
// as you type, and an alphabet row that jumps to a letter section.
//
// Server-rendered with the full list, then enhanced on mount: the input
// appears immediately (it's rendered SSR) and filtering is purely client-side
// so there is no round trip per keystroke.

export interface HubItem {
  slug: string
  name: string
  work_count: number
  nicknames?: string[]
}

function shortName(name: string): string {
  // Trim very long names for chip display
  return name.length > 40 ? name.slice(0, 38) + "…" : name
}

import type { ReactNode } from "react"

export default function HubFilter({
  items,
  basePath,
  label,
  hideTop,
  featured,
}: {
  items: HubItem[]
  basePath: string  // "/fandom" or "/ship"
  label: string     // "fandom" or "pairing" for aria
  hideTop?: boolean // hide the default "Most works" grid (page provides its own)
  featured?: ReactNode // custom section rendered between search bar and A–Z list
}) {
  const [query, setQuery] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(() => {
    if (!query.trim()) return items
    const q = query.toLowerCase()
    return items.filter(i => i.name.toLowerCase().includes(q))
  }, [items, query])

  // Group by first letter for the A-Z view
  const groups = useMemo(() => {
    const g = new Map<string, HubItem[]>()
    for (const h of filtered) {
      const first = h.name[0]?.toUpperCase() ?? "#"
      const key = /[A-Z]/.test(first) ? first : "#"
      if (!g.has(key)) g.set(key, [])
      g.get(key)!.push(h)
    }
    return g
  }, [filtered])

  const letters = [...groups.keys()].sort()
  const top = useMemo(() => [...items].sort((a, b) => b.work_count - a.work_count).slice(0, 30), [items])

  const clear = () => {
    setQuery("")
    inputRef.current?.focus()
  }

  return (
    <div className="hub-filter">
      {/* The filter input — always visible, even before JS hydrates. */}
      <div className="hub-filter__bar">
        <svg className="hub-filter__icon" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
          strokeLinejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" />
        </svg>
        <input
          ref={inputRef}
          type="search"
          className="hub-filter__input"
          placeholder={`Filter ${label}s…`}
          value={query}
          onChange={e => setQuery(e.target.value)}
          aria-label={`Filter ${label}s`}
          autoComplete="off"
          spellCheck={false}
        />
        {query && (
          <button className="hub-filter__clear" onClick={clear} aria-label="Clear filter">
            ×
          </button>
        )}
      </div>

      {query.trim() ? (
        <>
          <p className="hub-filter__count">
            {filtered.length === 0
              ? `No ${label}s match "${query}"`
              : `${filtered.length} ${label}${filtered.length === 1 ? "" : "s"} match "${query}"`}
          </p>
          {filtered.length > 0 && (
            <ul className="hub__az" role="list">
              {filtered.slice(0, 200).map(h => (
                <li key={h.slug}>
                  <Link href={`${basePath}/${h.slug}`}>{h.name}</Link>
                  {h.nicknames?.[0] && (
                    <span className="hub__az-aka">{h.nicknames[0]}</span>
                  )}{" "}
                  <span className="hub__az-count">{h.work_count.toLocaleString()}</span>
                </li>
              ))}
              {filtered.length > 200 && (
                <li className="hub-filter__more">
                  Showing first 200 — keep typing to narrow further
                </li>
              )}
            </ul>
          )}
        </>
      ) : (
        <>
          {/* Page-provided featured section (e.g. "Most read authors") —
              rendered after the search bar so search is always at the top. */}
          {featured}

          {/* Top by work count — what most visitors want. Hidden when the page
              provides its own featured section via the featured prop. */}
          {!hideTop && !featured && (
            <>
              <h2 className="hub__heading">Most works</h2>
              <ul className="hub__grid">
                {top.map(h => (
                  <li key={h.slug}>
                    <Link href={`${basePath}/${h.slug}`} className="hub__chip">
                      <span className="hub__chip-name" title={h.name}>{shortName(h.name)}</span>
                      <span className="hub__chip-count">{h.work_count.toLocaleString()}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}

          {/* Alphabet jump links — only shown when not filtering. */}
          <h2 className="hub__heading">All {label}s A–Z</h2>
          <p className="hub__alphabet">
            {letters.map(l => (
              <a key={l} href={`#letter-${l}`}>{l}</a>
            ))}
          </p>

          {letters.map(l => (
            <section key={l} className="hub__letter" id={`letter-${l}`}>
              <h3>{l}</h3>
              <ul className="hub__az">
                {groups.get(l)!.map(h => (
                  <li key={h.slug}>
                    <Link href={`${basePath}/${h.slug}`}>{h.name}</Link>
                    {h.nicknames?.[0] && (
                      <span className="hub__az-aka">{h.nicknames[0]}</span>
                    )}{" "}
                    <span className="hub__az-count">{h.work_count.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </div>
  )
}