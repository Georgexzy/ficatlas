"use client"
import { useEffect, useRef, useState } from "react"

const EXAMPLES = [
  { group: "Basic", rows: [
    { syntax: "harry potter slow burn", desc: "Free text — searches title, summary, author, fandoms, tags" },
    { syntax: "dramione >100k complete", desc: "Shorthand word count + status" },
    { syntax: "wip mature", desc: "Standalone rating + status words" },
  ]},
  { group: "Operators (quoted OR unquoted multi-word)", rows: [
    { syntax: "fandom: Harry Potter", desc: "Unquoted — reads until next operator" },
    { syntax: 'fandom:"My Hero Academia"', desc: "Quoted — same result" },
    { syntax: "ship:Draco/Hermione", desc: "Relationship (also: pairing: rel:)" },
    { syntax: "char: Hermione Granger", desc: "Character (also: character:)" },
    { syntax: 'tag: slow burn', desc: "Additional tag (also: t:)" },
    { syntax: "rating:M", desc: "G, T, M, E, NR or general/teen/mature/explicit" },
    { syntax: "status:complete", desc: "complete / wip / completed / ongoing" },
    { syntax: "lang:French", desc: "Language" },
    { syntax: "site:ao3", desc: "Specific site only (ao3 / ffnet / fictionalley)" },
  ]},
  { group: "Word Count", rows: [
    { syntax: ">100k", desc: "Over 100,000 words" },
    { syntax: "<50k", desc: "Under 50,000 words" },
    { syntax: "words:100k-200k", desc: "Between 100k and 200k" },
    { syntax: "wc:>200k", desc: "Operator prefix (also: word: w:)" },
  ]},
  { group: "Date", rows: [
    { syntax: "updated:1y", desc: "Updated within the last year" },
    { syntax: "updated:6m", desc: "Updated within 6 months" },
    { syntax: "since:2024", desc: "Since Jan 2024" },
  ]},
  { group: "Exclude (prefix with -)", rows: [
    { syntax: "-tag:fluff", desc: "Exclude tag" },
    { syntax: "-fandom: twilight", desc: "Exclude fandom" },
    { syntax: "-ship:Harry/Ginny", desc: "Exclude pairing" },
  ]},
  { group: "Combined example", rows: [
    { syntax: 'fandom: Harry Potter ship:Draco/Hermione >100k complete updated:2y -tag:fluff',
      desc: "Everything at once — operators in any order" },
  ]},
]

export default function SyntaxHelp() {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement | null>(null)
  const btnRef   = useRef<HTMLButtonElement | null>(null)

  // Close on Escape or outside-click
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node
      if (panelRef.current?.contains(t)) return         // click inside panel
      if (btnRef.current?.contains(t)) return           // click on the trigger toggles
      setOpen(false)
    }
    window.addEventListener("keydown", onKey)
    // capture phase so we close even if a child stops propagation
    document.addEventListener("click", onClick, true)
    return () => {
      window.removeEventListener("keydown", onKey)
      document.removeEventListener("click", onClick, true)
    }
  }, [open])

  return (
    <div className="syntax-help">
      <button ref={btnRef} className="syntax-help__btn" onClick={() => setOpen(o => !o)}
              title="Search syntax help" aria-label="Search syntax help">?</button>

      {open && (
        <>
          <div className="syntax-help__backdrop" aria-hidden="true" />
          <div ref={panelRef} className="syntax-help__panel" role="dialog" aria-modal="true">
            <div className="syntax-help__header">
              <p className="syntax-help__title">Search Syntax</p>
              <button className="syntax-help__close" onClick={() => setOpen(false)} aria-label="Close">✕</button>
            </div>
            <p className="syntax-help__intro">
              Operators work in any order. Quotes optional for multi-word values — unquoted values
              read until the next operator. Click outside or press <kbd>Esc</kbd> to close.
            </p>
            {EXAMPLES.map(group => (
              <div key={group.group} className="syntax-help__group">
                <p className="syntax-help__group-label">{group.group}</p>
                {group.rows.map(row => (
                  <div key={row.syntax} className="syntax-help__row">
                    <code className="syntax-help__code">{row.syntax}</code>
                    <span className="syntax-help__desc">{row.desc}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
