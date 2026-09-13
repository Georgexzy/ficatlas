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

interface Extracted {
  terms: { kind: string; value: string; count: number; matched: string }[]
  query: string
  ignored_words: number
  // Read from the post as FILTERS rather than as words to search for. The
  // status and the word count are already inside `query` as the search bar's
  // own shorthand (`wip`, `words:>150k`), so they are visible in the box and
  // travel with any link. `sort` cannot be written in the bar at all, so it is
  // passed as a parameter and shown here — otherwise the one thing the reader
  // asked for most plainly ("any GOOD fics") would be invisible and silently
  // dropped.
  status?: string | null
  sort?: string | null
  word_count_min?: number | null
  word_count_max?: number | null
}

interface Taste {
  matched: { asked: string; id: string; title: string; site: string; kudos: number }[]
  unmatched: string[]
  tags: { value: string; kind: string; shared_by: number; works: number }[]
  query: string
}

// The "I have already read" list, which these posts almost always carry and
// which is the richest signal in the whole request — far better than the
// adjectives. Somebody who names three Slytherin!Harry fics has told you what
// they want in the index's own vocabulary.
const READ_LIST = /\b(?:i(?:'ve| have)?\s+(?:already\s+)?read|already\s+read|read\s+so\s+far|stories\s+i(?:'ve| have)\s+read)\b/i

/** The titles under an "I have read" heading, one per line. */
function readTitles(raw: string): string[] {
  const lines = raw.split(/[\r\n]+/)
  const start = lines.findIndex(l => READ_LIST.test(l))
  if (start < 0) return []
  return lines.slice(start + 1)
    .map(l => l.replace(/^\s*[-*•\d.)\s]+/, "")
                .replace(/\s*[-–—]\s*(enjoyed|loved|liked|great|good|meh).*$/i, "")
                .trim())
    .filter(l => l.length > 2 && l.length < 90)
    .slice(0, 10)
}

// Parameters and operators that must never appear in a link this panel hands
// somebody to paste in public. A reader was banned for fourteen days from
// r/HPFanfiction for posting a FicAtlas search that listed works tagged
// "Underage Sex"; the rules of most fandom spaces, and Reddit's own policy,
// make the person who pasted the link responsible for what it shows.
//
// Stripped here rather than merely "not added", because the query box is free
// text and an operator typed into it would otherwise ride along.
const UNSAFE_IN_A_LINK = /\b(?:include_underage|explicit)\s*[:=]\s*(?:true|1|yes)\b/gi

/** The search as a reader would link to it — always in the safe default.
 *
 *  The SORT rides along, because it is the only part of an extracted request
 *  the search bar cannot express. A post saying "any good fics" is asking for
 *  the works other readers actually read, and a link without `sort` answers a
 *  different question from the one on screen. */
const publicLink = (q: string, sort?: string | null) => {
  const safe = q.replace(UNSAFE_IN_A_LINK, "").replace(/\s+/g, " ").trim()
  const base = `${PUBLIC}/?q=${encodeURIComponent(safe).replace(/%20/g, "+")}`
  return sort ? `${base}&sort=${encodeURIComponent(sort)}` : base
}

/** Did the operator ask for something a shared link must not carry? */
const asksForUnsafe = (q: string) => UNSAFE_IN_A_LINK.test(q)

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

// The negative half of a request, kept rather than stripped.
//
// Real posts are as much about what the reader does not want as what they do —
// one in the sample corpus listed nine negative conditions against six
// positive. The backend now reads "no X" and "without X" as exclusions (see
// _extract_negations in query_intent.py), so these lines are worth keeping in
// the condensed query instead of being thrown away with the rest of the prose.
const NEGATIVE_LINE = /\b(?:no|not|without|excluding|avoid|don'?t want)\b/i

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

/** The lines of a post that say what the reader does NOT want.
 *
 * Offered as one-click additions rather than merged into the query, because a
 * post's negative list is usually longer than any search should be and the
 * person running the tool is better placed than a regex to pick the two that
 * matter. */
function negativeLines(raw: string): string[] {
  return raw
    .split(/[\r\n]+/)
    .map(l => l.replace(/^\s*[-*•]\s*/, "").trim())
    .filter(l => l.length > 2 && l.length < 60 && NEGATIVE_LINE.test(l))
    .slice(0, 6)
}

export default function OutreachPanel() {
  const [raw, setRaw] = useState("")
  const [q, setQ] = useState("")
  const [found, setFound] = useState("")
  const [res, setRes] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const [taste, setTaste] = useState<Taste | null>(null)
  const [ext, setExt] = useState<Extracted | null>(null)

  const run = useCallback(async (query: string, sort?: string | null) => {
    const term = query.trim()
    if (!term) return
    setBusy(true); setErr(null)
    try {
      // The public endpoint, so what is shown here is exactly what a reader
      // following the link will see. Anything else would be a demo of a
      // different site.
      //
      // `sort` is passed because /api/search/extract derives it and nothing
      // else can carry it: the status and the word count go into the query
      // string as the bar's own shorthand, but quality is not expressible
      // there. This panel used to read `query` alone and drop every other
      // field the endpoint returned, so a post asking for "good ongoing fics,
      // at least 150k words" was searched with none of the three.
      const r = await fetch(
        `/api/search?q=${encodeURIComponent(term)}&per_page=8` +
        (sort ? `&sort=${encodeURIComponent(sort)}` : ""),
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

  // A reply is only offered once the search has actually been run and actually
  // found something.
  //
  // This shipped without the guard and immediately did the one thing the rules
  // on this very page forbid: a post asking for happy Harry/Daphne fics was
  // condensed into a forty-word query, matched nothing, and the panel cheerfully
  // produced "Try this: <link>" pointing at an empty results page. Posting that
  // is worse than posting nothing — it is an advert that also wastes the
  // reader's click, and it is exactly the "I couldn't find it, but try this
  // site" reply the guidance below calls an advert.
  //
  // `searched` distinguishes "no results" from "not looked yet", so the panel
  // does not accuse an untouched box of having failed.
  const searched = res !== null
  const foundSomething = searched && (res!.total ?? 0) > 0
  // A link is generated in the safe default whatever the box says, so a query
  // asking for gated content would produce a link that does not match what is
  // on screen. Better to say so than to hand over a link quietly different
  // from the search that was run.
  const unsafeQuery = asksForUnsafe(q)
  const reply = (!foundSomething || unsafeQuery) ? null : found.trim()
    ? `I think this is ${found.trim()}.\n\nFound it here if you want to check: ${publicLink(q, ext?.sort)}\n(searches AO3, FanFiction.net and FictionAlley together)`
    : `Try this: ${publicLink(q, ext?.sort)}\n\nIt searches AO3, FanFiction.net and FictionAlley at once — worth a look if it might not be on AO3.`

  const copy = async (text: string | null, what: string) => {
    if (!text) return
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
        {/* EXTRACT, not condense.
            Stripping framing from a two-hundred-word post leaves a
            hundred-and-eighty-word query, and every term is a requirement, so
            it matches nothing — observed on a real post asking for happy
            Harry/Daphne, which returned zero while
            `harry potter daphne greengrass fluff` returns 1,058. The index
            knows its own vocabulary; asking it which words are searchable
            beats guessing which ones were framing. */}
        <button className="btn btn--primary" disabled={!raw.trim()}
          onClick={async () => {
            setErr(null)
            try {
              const r = await fetch("/api/search/extract?text=" +
                encodeURIComponent(raw.slice(0, 4000)), { credentials: "include" })
              if (!r.ok) throw new Error(`Could not read the post (${r.status})`)
              const e: Extracted = await r.json()
              setExt(e)
              if (e.query) { setQ(e.query); run(e.query, e.sort) }
              else setErr("Nothing in that post matches a tag, character or fandom the index knows.")
            } catch (e: any) { setErr(e.message) }
          }}>
          Find the searchable terms
        </button>
        <span className="outreach__hint">
          Matches the post against the index&rsquo;s own vocabulary. Then click
          the terms below to build the search — two or three beats forty words.
        </span>
      </div>

      {/* Offered, not applied. Extraction from prose is genuinely ambiguous —
          "for the love of God" really does contain a character this index
          knows — so the shortlist is shown and a person decides. */}
      {ext?.sort && (
        <p className="outreach__hint">
          The post asked for good fics, so this is sorted by what readers
          actually read. The status and length it named are in the box.
        </p>
      )}

      {ext && ext.terms.length > 0 && (
        <div className="outreach__row outreach__negs">
          <span className="outreach__hint">In the post:</span>
          {ext.terms.map(t => {
            const op = t.kind === "relationship" ? "ship"
                     : t.kind === "character" ? "char"
                     : t.kind === "fandom" ? "fandom" : "tag"
            const token = `${op}:"${t.value}"`
            const on = q.includes(token)
            return (
              <button key={t.kind + t.value}
                className={"btn" + (on ? " btn--primary" : "")}
                title={`${t.count.toLocaleString()} works · from "${t.matched}"`}
                onClick={() => setQ(cur => on
                  ? cur.replace(token, "").replace(/\s+/g, " ").trim()
                  : `${cur} ${token}`.replace(/\s+/g, " ").trim())}>
                {t.value} <small>{t.count.toLocaleString()}</small>
              </button>
            )
          })}
        </div>
      )}

      {/* The "I have already read" half.
          Resolved against the index, then reduced to the tags those works have
          IN COMMON — one work's tag list describes that work; what several
          share is taste. The works themselves are then excluded from the
          search, since recommending back what somebody has told you they have
          read is the one answer they have ruled out. */}
      {readTitles(raw).length > 0 && (
        <div className="outreach__row outreach__negs">
          <button className="btn" onClick={async () => {
            try {
              const r = await fetch("/api/search/taste?titles=" +
                encodeURIComponent(readTitles(raw).join("|")),
                { credentials: "include" })
              if (!r.ok) throw new Error(`Could not read their list (${r.status})`)
              const t: Taste = await r.json()
              setTaste(t)
              if (t.query) { setQ(t.query); run(t.query, ext?.sort) }
            } catch (e: any) { setErr(e.message) }
          }}>
            Use the {readTitles(raw).length} fics they&rsquo;ve read →
          </button>
          <span className="outreach__hint">
            Finds what those works have in common and searches for more of it.
          </span>
        </div>
      )}

      {taste && (
        <p className="outreach__hint">
          {taste.matched.length > 0 && (
            <>Matched <strong>{taste.matched.map(m => m.title).join(", ")}</strong>. </>
          )}
          {taste.tags.length > 0 && (
            <>Shared: {taste.tags.map(t => `${t.value} (${t.shared_by})`).join(" · ")}. </>
          )}
          {taste.unmatched.length > 0 && (
            <>Not in the index: {taste.unmatched.join(", ")}. </>
          )}
          {taste.tags.length === 0 && taste.matched.length > 0 &&
            <>They share no tags — too different to derive a taste from.</>}
        </p>
      )}

      {/* The "not looking for" half, which is where these posts carry most of
          their information and which the tool previously dropped on the floor.
          One click each, because a post's negative list is longer than any
          search should be. */}
      {negativeLines(raw).length > 0 && (
        <div className="outreach__row outreach__negs">
          <span className="outreach__hint">They said no to:</span>
          {negativeLines(raw).map(l => (
            <button key={l} className="btn"
              title="Add this to the search as an exclusion"
              onClick={() => setQ(q => `${q} ${l}`.replace(/\s+/g, " ").trim())}>
              {l.length > 34 ? l.slice(0, 33) + "…" : l}
            </button>
          ))}
        </div>
      )}

      <label className="outreach__label" htmlFor="outreach-q">Search</label>
      <div className="outreach__row">
        <input id="outreach-q" className="outreach__q" value={q}
          placeholder="powerful harry dumbledore bashing"
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") run(q, ext?.sort) }} />
        <button className="btn btn--primary" onClick={() => run(q, ext?.sort)} disabled={busy || !q.trim()}>
          {busy ? "Searching…" : "Search"}
        </button>
        <a className="btn" href={publicLink(q, ext?.sort)} target="_blank" rel="noopener noreferrer">
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
                  onClick={() => { setQ(s.query); run(s.query, ext?.sort) }}>
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

      {reply ? (
        <>
          <pre className="outreach__reply">{reply}</pre>
          <div className="outreach__row">
            <button className="btn btn--primary" onClick={() => copy(reply, "reply")}>
              Copy reply
            </button>
            <button className="btn" onClick={() => copy(publicLink(q, ext?.sort), "link")}>
              Copy link only
            </button>
            {copied && <span className="outreach__copied">Copied {copied}</span>}
          </div>
        </>
      ) : (
        <p className="outreach__blocked">
          {unsafeQuery
            ? "This search asks for content that must not appear in a link " +
              "you paste in public — that is what got the r/HPFanfiction ban. " +
              "Remove it from the box and search again."
            : !searched
            ? "Run a search first — the reply is built from what it finds."
            : "This search found nothing, so there is no reply to send. " +
              "Narrow it to two or three of the terms above, or close the tab: " +
              "a link to an empty page is an advert, not an answer."}
        </p>
      )}

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
