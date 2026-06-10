"use client"
import { useState } from "react"

const EXAMPLES = [
  {
    group: "Basic",
    rows: [
      { syntax: "harry potter slow burn",         desc: "Free text — searches title & summary" },
      { syntax: "dramione >100k complete",        desc: "Shorthand word count + status" },
      { syntax: "wip mature",                     desc: "Standalone rating and status words" },
    ]
  },
  {
    group: "Operators",
    rows: [
      { syntax: 'fandom:"Harry Potter"',          desc: "Exact fandom (quote multi-word)" },
      { syntax: "ship:Draco/Hermione",            desc: "Relationship / pairing (also: pairing: rel:)" },
      { syntax: "char:Hermione",                  desc: "Character filter (also: character:)" },
      { syntax: 'tag:"slow burn"',                desc: "Additional tag (also: t:)" },
      { syntax: "rating:M",                       desc: "Rating: G T M E NR (also: mature, teen…)" },
      { syntax: "status:complete",                desc: "Status (also: wip, completed, ongoing)" },
      { syntax: "lang:French",                    desc: "Language filter" },
      { syntax: "site:ao3",                       desc: "Specific site only (ao3 / ffnet)" },
    ]
  },
  {
    group: "Word Count",
    rows: [
      { syntax: ">100k",                          desc: "Over 100,000 words" },
      { syntax: "<50k",                           desc: "Under 50,000 words" },
      { syntax: "words:100k-200k",                desc: "Between 100k and 200k" },
      { syntax: "wc:>200k",                       desc: "Operator prefix (also: word: w:)" },
    ]
  },
  {
    group: "Date",
    rows: [
      { syntax: "updated:1y",                     desc: "Updated within the last year" },
      { syntax: "updated:6m",                     desc: "Updated within 6 months" },
      { syntax: "since:2024",                     desc: "Updated since Jan 2024" },
    ]
  },
  {
    group: "Exclude (prefix with -)",
    rows: [
      { syntax: "-tag:fluff",                     desc: "Exclude stories tagged 'fluff'" },
      { syntax: "-fandom:twilight",               desc: "Exclude a fandom" },
      { syntax: "-ship:Harry/Ginny",              desc: "Exclude a pairing" },
    ]
  },
  {
    group: "Crossovers",
    rows: [
      { syntax: "crossover:only",                 desc: "Crossovers only" },
      { syntax: "crossover:no",                   desc: "No crossovers" },
      { syntax: "xover:only",                     desc: "Shorthand alias" },
    ]
  },
  {
    group: "Combined example",
    rows: [
      {
        syntax: 'fandom:"Harry Potter" ship:Draco/Hermione >100k complete updated:2y -tag:fluff',
        desc: "HP Dramione, 100k+, complete, updated in last 2 years, no fluff"
      },
    ]
  }
]

export default function SyntaxHelp() {
  const [open, setOpen] = useState(false)

  return (
    <div className="syntax-help">
      <button className="syntax-help__btn" onClick={() => setOpen(o => !o)} title="Search syntax help">
        ?
      </button>

      {open && (
        <>
          <div className="syntax-help__backdrop" onClick={() => setOpen(false)} />
          <div className="syntax-help__panel">
            <div className="syntax-help__header">
              <p className="syntax-help__title">Search Syntax</p>
              <button className="syntax-help__close" onClick={() => setOpen(false)}>✕</button>
            </div>
            <p className="syntax-help__intro">
              Operators can appear anywhere in the search bar, in any order. Quote multi-word values.
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
