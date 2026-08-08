"use client"
import { useEffect, useRef, useState } from "react"

// Search syntax help, as a panel that DROPS UNDER THE SEARCH BAR and whose
// examples are clickable.
//
// It used to be a full-screen modal listing the same reference table. Two
// problems with that shape, beyond it being heavy for "what can I type here":
//
//   * it covered the search box, so you read the syntax, dismissed the dialog,
//     and then had to remember and retype it;
//   * being modal it needed a backdrop, a focus trap and a scroll lock to
//     behave, and every one of those was somewhere it could go wrong.
//
// Anchored under the bar instead, the box you are about to type in stays
// visible, and clicking an example INSERTS it — so the panel builds the query
// rather than describing it. Nothing is modal, so there is nothing to trap.
type Row = { syntax: string; desc: string; insert?: string }

const GROUPS: { group: string; rows: Row[] }[] = [
  { group: "Narrow it down", rows: [
    { syntax: "fandom:", desc: "A fandom — quotes optional", insert: "fandom:" },
    { syntax: "ship:", desc: "A pairing, e.g. Draco/Hermione", insert: "ship:" },
    { syntax: "char:", desc: "A character", insert: "char:" },
    { syntax: "tag:", desc: "Any additional tag", insert: "tag:" },
    { syntax: "author:", desc: "Everything by one author", insert: "author:" },
  ]},
  { group: "Length & status", rows: [
    { syntax: ">100k", desc: "Over 100,000 words", insert: ">100k" },
    { syntax: "<50k", desc: "Under 50,000 words", insert: "<50k" },
    { syntax: "words:100k-200k", desc: "Between two sizes", insert: "words:100k-200k" },
    { syntax: "complete", desc: "Finished works", insert: "complete" },
    { syntax: "wip", desc: "Still updating", insert: "wip" },
  ]},
  { group: "Rating, language, site", rows: [
    { syntax: "rating:M", desc: "G / T / M / E / NR", insert: "rating:M" },
    { syntax: "lang:French", desc: "Any language name", insert: "lang:" },
    { syntax: "site:ao3", desc: "ao3 / ffnet / fictionalley", insert: "site:ao3" },
    // Only meaningful alongside site:fictionalley, and listed anyway: the
    // sidebar hides the filter until FictionAlley is selected, so this panel is
    // where someone finds out the subsites exist at all.
    { syntax: "subsite:Schnoogle", desc: "A FictionAlley subsite — Schnoogle, The Dark Arts, Astronomy Tower, Riddikulus, Essays & Meta",
      insert: "subsite:" },
    { syntax: "series:true", desc: "Part of a series", insert: "series:true" },
    { syntax: "series:false", desc: "Standalone — not in a series", insert: "series:false" },
  ]},
  { group: "When it was updated", rows: [
    { syntax: "updated:1y", desc: "Within the last year", insert: "updated:1y" },
    { syntax: "updated:6m", desc: "Within six months", insert: "updated:6m" },
    { syntax: "since:2024", desc: "Since a given year", insert: "since:2024" },
  ]},
  { group: "Whole searches to start from", rows: [
    { syntax: "ship:Draco/Hermione >100k complete",
      desc: "A pairing, long, finished" },
    { syntax: "fandom: Harry Potter marauders wip updated:2y",
      desc: "Still updating, touched in the last two years" },
    { syntax: "fandom: Harry Potter -tag:fluff rating:M words:>50k",
      desc: "Everything at once — operators in any order" },
    { syntax: "subsite:Schnoogle >100k complete",
      desc: "Long finished novels from FictionAlley's novel shelf" },
  ]},
  { group: "Leave things out", rows: [
    { syntax: "-tag:fluff", desc: "Exclude — prefix any operator with a minus", insert: "-tag:" },
    { syntax: "-ship:Harry/Ginny", desc: "Exclude a pairing", insert: "-ship:" },
  ]},
]

export default function SyntaxHelp({ onInsert }: { onInsert?: (text: string) => void }) {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement | null>(null)
  const btnRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); btnRef.current?.focus() }
    }
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node
      if (panelRef.current?.contains(t)) return
      if (btnRef.current?.contains(t)) return   // the trigger toggles itself
      setOpen(false)
    }
    window.addEventListener("keydown", onKey)
    // capture phase so we still close if a child stops propagation
    document.addEventListener("click", onClick, true)
    return () => {
      window.removeEventListener("keydown", onKey)
      document.removeEventListener("click", onClick, true)
    }
  }, [open])

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="syntax-help__btn"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-label="Search syntax help"
        title="Search syntax help"
      >?</button>

      {open && (
        <div ref={panelRef} className="syntax-panel" role="dialog"
          aria-label="Search syntax help">
          <div className="syntax-panel__head">
            <div>
              <p className="syntax-panel__title">What you can type</p>
              <p className="syntax-panel__hint">Click any example to add it to your search</p>
            </div>
            <button className="syntax-panel__close"
              onClick={() => { setOpen(false); btnRef.current?.focus() }}
              aria-label="Close syntax help">✕</button>
          </div>

          <div className="syntax-panel__body">
            {GROUPS.map(g => (
              <div key={g.group} className="syntax-panel__group">
                <p className="syntax-panel__group-label">{g.group}</p>
                <div className="syntax-panel__rows">
                  {g.rows.map(r => (
                    <button
                      key={r.syntax}
                      type="button"
                      className="syntax-panel__row"
                      onClick={() => onInsert?.(r.insert ?? r.syntax)}
                      title={`Add "${r.insert ?? r.syntax}" to your search`}
                    >
                      <code className="syntax-panel__code">{r.syntax}</code>
                      <span className="syntax-panel__desc">{r.desc}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <p className="syntax-panel__foot">
            Operators work in any order, and quotes are optional — an unquoted
            value reads until the next operator. <kbd>Esc</kbd> closes.
          </p>
        </div>
      )}
    </>
  )
}
