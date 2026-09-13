"use client"

import { useState, useCallback } from "react"
import { SITE_LABELS, formatNumber } from "@/lib/api"

/**
 * Turning a fic-finder post into a search, and a search into a reply.
 *
 * The site's whole distribution problem is that nobody has heard of it, and the
 * one audience that is already asking the question FicAtlas answers is the
 * fic-finding threads — r/FanFiction's Lost Fic flair, r/AO3's Fic/Work Search,
 * and the Tumblr blogs that have done the same job since LiveJournal. Answering
 * those is the highest-fidelity outreach available: the reply is useful on its
 * own terms, and the link IS the demonstration.
 *
 * It lives here rather than in a document because the work is iterative. A post
 * lists four conditions; the first search is rarely the right one; you try
 * three. A page that runs the search, shows the archive split and writes the
 * reply makes that loop seconds long instead of a tab-juggle.
 *
 * It only ever READS the public search API — the same endpoint any visitor
 * hits — and it posts nothing anywhere. The reply is text on a clipboard; a
 * person decides whether it is worth sending.
 */

interface Row {
  id: string
  title: string
  site: string
  word_count?: number | null
  kudos?: number | null
  url?: string | null
}

interface Result {
  total: number
  count_is_capped?: boolean
  site_counts?: Record<string, number>
  results: Row[]
  suggestions?: { value: string; kind: string; count: number; query: string }[]
}

const PUBLIC = "https://ficatlas.com"

/** The search as a reader would link to it. */
const publicLink = (q: string) =>
  `${PUBLIC}/?q=${encodeURIComponent(q).replace(/%20/g, "+")}`

// Framing that carries no information about any story. Stripped from a pasted
// post before it reaches the box, because every word in a search is a
// requirement and "looking for a fic where" is four of them.
//
// This is a HINT, not a parser: the backend's query_intent.py does the real
// work and does it better. The point here is only to get a 40-line post down to
// something a person can look at and edit.
const FRAMING = [
  /^\s*(?:the\s+fic\s+should\s+be|i(?:'m| am)?\s+looking\s+for|looking\s+for|does\s+anyone\s+(?:know|remember)|help|fic\s*search|searching\s+for|trying\s+to\s+find|wanted)\b[:,]?/gi,
  /\b(?:please|pls|thanks|thank\s+you|tia|any\s+(?:help|recs?)|would\s+be\s+(?:great|appreciated))\b/gi,
  /\b(?:a\s+)?fics?\s+where\b/gi,
  /\b(?:i\s+(?:have\s+)?(?:already\s+)?read|i've\s+read|already\s+read)\b[\s\S]*$/gi,
]

/** A pasted post, reduced to something worth searching. */
function condense(raw: string): string {
  let t = raw
  for (const rx of FRAMING) t = t.replace(rx, " ")
  return t
    .replace(/^\s*[-*•]\s*/gm, " ")     // bullet markers
    .replace(/[\r\n]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 220)
}

export default function OutreachPanel() {
  const [raw, setRaw] = useState("")
  const [q, setQ] = useState("")
  const [found, setFound] = useState("")
  const [res, setRes] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  const run = useCallback(async (query: string) => {
    const term = query.trim()
    if (!term) return
    setBusy(true); setErr(null)
    try {
      // The public endpoint, so what is shown here is exactly what a reader
      // following the link will see. Anything else would be a demo of a
      // different site.
      const r = await fetch(
        `/api/search?q=${encodeURIComponent(term)}&per_page=8`,
        { credentials: "include" })
      if (!r.ok) throw new Error(`Search failed (${r.status})`)
      setRes(await r.json())
    } catch (e: any) {
      setErr(e.message || "Search failed"); setRes(null)
    } finally { setBusy(false) }
  }, [])

  const sites = res?.site_counts ?? {}
  const archives = Object.entries(sites).filter(([, n]) => n > 0)
                         .sort((a, b) => b[1] - a[1])
  // The claim the whole site rests on. A search that only finds AO3 works is a
  // true answer and a poor advertisement, so the panel says which it is rather
  // than leaving it to be noticed.
  const multiArchive = archives.length >= 2

  const reply = found.trim()
    ? `I think this is ${found.trim()}.\n\nFound it here if you want to check: ${publicLink(q)}\n(searches AO3, FanFiction.net and FictionAlley together)`
    : `Try this: ${publicLink(q)}\n\nIt searches AO3, FanFiction.net and FictionAlley at once — worth a look if it might not be on AO3.`

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
      setTimeout(() => setCopied(null), 2000)
    } catch {
      setErr("Could not copy — select the text and use Ctrl+C.")
    }
  }

  return (
    <div className="outreach">
      <p className="admin-note">
        Turn a fic-finder post into a search you can link. Reads the public
        search API and posts nothing anywhere — the reply is text on your
        clipboard, and you decide whether it is worth sending.
      </p>

      {/* Paste first, because that is what you actually have: a post. */}
      <label className="outreach__label" htmlFor="outreach-raw">
        Paste the post
      </label>
      <textarea id="outreach-raw" className="outreach__paste" value={raw}
        placeholder={"at least 150k words - very long\nharry is lord of at least 2 houses\nmagically and politically powerful\nbashing (dumbles/weasleys/hermione)"}
        onChange={e => setRaw(e.target.value)} />
      <div className="outreach__row">
        <button className="btn btn--primary" disabled={!raw.trim()}
          onClick={() => { const c = condense(raw); setQ(c); run(c) }}>
          Condense &amp; search
        </button>
        <span className="outreach__hint">
          Strips &ldquo;looking for a fic where&rdquo;, bullets and the
          &ldquo;I have read&rdquo; list. Then edit the box below — two or three
          tropes beats forty words.
        </span>
      </div>

      <label className="outreach__label" htmlFor="outreach-q">Search</label>
      <div className="outreach__row">
        <input id="outreach-q" className="outreach__q" value={q}
          placeholder="powerful harry dumbledore bashing"
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") run(q) }} />
        <button className="btn btn--primary" onClick={() => run(q)} disabled={busy || !q.trim()}>
          {busy ? "Searching…" : "Search"}
        </button>
        <a className="btn" href={publicLink(q)} target="_blank" rel="noopener noreferrer">
          Open on site
        </a>
      </div>

      {err && <div className="alert alert--error" role="alert">{err}</div>}

      {res && (
        <>
          <div className="outreach__verdict">
            <span className="outreach__total">
              {res.count_is_capped ? "5,000+" : formatNumber(res.total)} works
            </span>
            {archives.map(([s, n]) => (
              <span key={s} className={`outreach__chip outreach__chip--${s}`}>
                {formatNumber(n)} {SITE_LABELS[s] ?? s}
              </span>
            ))}
            {/* Said plainly rather than left to be inferred from a row of
                chips. A search that finds one archive is a true answer and a
                weak demonstration — the pitch is that three were searched. */}
            <span className={`outreach__flag ${multiArchive ? "is-good" : "is-weak"}`}>
              {res.total === 0 ? "nothing found — try fewer words"
                : multiArchive ? "spans archives — good one to link"
                : res.count_is_capped
                  ? "too broad to show the split — narrow it"
                  : "one archive only — weaker link"}
            </span>
          </div>

          {/* The site's own spelling rescue, surfaced here because a fic-finder
              post is full of half-remembered names. */}
          {res.suggestions && res.suggestions.length > 0 && (
            <div className="outreach__row outreach__suggest">
              <span className="outreach__hint">Did you mean:</span>
              {res.suggestions.map(s => (
                <button key={s.value} className="btn"
                  onClick={() => { setQ(s.query); run(s.query) }}>
                  {s.value} <small>{formatNumber(s.count)}</small>
                </button>
              ))}
            </div>
          )}

          {res.results.length > 0 && (
            <table className="traffic-table outreach__results">
              <thead><tr>
                <th>Title</th><th>Archive</th>
                <th className="num">Words</th><th className="num">Kudos</th>
              </tr></thead>
              <tbody>
                {res.results.map(r => (
                  <tr key={r.id}>
                    <td>
                      <a href={`${PUBLIC}/story/${r.id}`} target="_blank" rel="noopener noreferrer">
                        {r.title}
                      </a>
                    </td>
                    <td>{SITE_LABELS[r.site] ?? r.site}</td>
                    <td className="num">{r.word_count ? formatNumber(r.word_count) : "—"}</td>
                    <td className="num">{r.kudos ? formatNumber(r.kudos) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <label className="outreach__label" htmlFor="outreach-found">
        The fic, if you found it
      </label>
      <div className="outreach__row">
        <input id="outreach-found" className="outreach__q" value={found}
          placeholder="Family Bonds — on FanFiction.net"
          onChange={e => setFound(e.target.value)} />
      </div>
      <p className="outreach__hint">
        Naming the actual fic is the reply. Everything else is a footnote — and
        if the search did not find what they described, post nothing at all.
      </p>

      <pre className="outreach__reply">{reply}</pre>
      <div className="outreach__row">
        <button className="btn btn--primary" onClick={() => copy(reply, "reply")}>
          Copy reply
        </button>
        <button className="btn" onClick={() => copy(publicLink(q), "link")}>
          Copy link only
        </button>
        {copied && <span className="outreach__copied">Copied {copied}</span>}
      </div>

      {/* The rules, on the screen where the reply is written rather than in a
          document that will not be open at the time. */}
      <details className="admin-group">
        <summary className="admin-group__summary">Posting rules worth not breaking</summary>
        <ul className="outreach__rules">
          <li><strong>Answer first.</strong> Name the fic, author and archive. If the search did not find it, say nothing — &ldquo;I couldn&rsquo;t find it, but try this site&rdquo; is an advert.</li>
          <li><strong>Link the search, not the home page.</strong> The search URL is the evidence; they click it and the fic is there.</li>
          <li><strong>Mention it once, plainly.</strong> No pitch, no feature list.</li>
          <li><strong>Not every thread.</strong> A new account answering twenty posts with one domain reads as spam whatever the intent.</li>
          <li><strong>Say yes if asked whether you built it.</strong> Most subs are fine with a useful maker; none forgive one who pretended not to be.</li>
          <li><strong>Read each sub&rsquo;s self-promotion rule first.</strong> r/FanFiction and r/AO3 differ and both change.</li>
        </ul>
      </details>
    </div>
  )
}
