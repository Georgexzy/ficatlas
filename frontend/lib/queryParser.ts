/**
 * FicAtlas client-side query parser — mirrors backend query_parser.py
 * Handles unquoted multi-word values: fandom: Harry Potter
 */

export interface ParsedToken {
  key: string
  value: string
  exclude: boolean
  raw: string
}

export interface ParsedQuery {
  /** FictionAlley sections (Schnoogle, The Dark Arts, …). */
  sections: string[]
  cleanText: string
  fandoms: string[]
  relationships: string[]
  characters: string[]
  tags: string[]
  ratings: string[]
  warnings: string[]
  categories: string[]
  sites: string[]
  excFandoms: string[]
  excRelationships: string[]
  excCharacters: string[]
  excTags: string[]
  status: string | null
  language: string | null
  /** Exact author match. The syntax panel advertises `author:`; without this it
      fell through to free text, so author:astolat found 1 work instead of 150. */
  author: string | null
  wordCountMin: number | null
  wordCountMax: number | null
  updatedAfter: string | null
  crossovers: string | null
  /** true = in a series, false = standalone, null = either. */
  inSeries: boolean | null
  tokens: ParsedToken[]
}

const FIELD_ALIASES: Record<string, string> = {
  fandom: "fandoms", fandoms: "fandoms", f: "fandoms",
  ship: "relationships", pairing: "relationships", rel: "relationships",
  relationship: "relationships", relationships: "relationships",
  char: "characters", character: "characters", characters: "characters",
  tag: "tags", tags: "tags", t: "tags",
  rating: "ratings", ratings: "ratings", r: "ratings",
  status: "status", s: "status",
  word: "word_count", words: "word_count", wc: "word_count", w: "word_count",
  lang: "language", language: "language",
  author: "author", by: "author",
  updated: "updated_after", update: "updated_after", since: "updated_after",
  site: "sites",
  // FictionAlley subsites, so the bar round-trips: the sidebar writes
  // subsite:Schnoogle and typing the same thing has to mean the same thing.
  // `section:` was the spelling the sidebar emitted first and is still accepted
  // — anyone who bookmarked or shared such a URL keeps a working link.
  subsite: "sections", subsites: "sections",
  section: "sections", sections: "sections",
  crossover: "crossovers", xover: "crossovers",
  warn: "warnings", warning: "warnings", warnings: "warnings",
  category: "categories", cat: "categories",
  // series:true / series:false — what the bar writes. in_series: accepted too,
  // because that is the URL query-param name and people mirror what they see.
  series: "in_series", in_series: "in_series",
}

// Archive names → the three values stored in stories.site. Must stay in step
// with SITE_ALIASES in backend/query_parser.py: this parser decides what the
// bar shows and what goes into the URL, that one decides what a shared URL
// means when the API re-parses it, and a name only one of them knows is a
// filter that works in one direction.
//
// Without this the value was merely lowercased, so `site:ao3` worked and
// `site:FF.net`, `site:fanfiction.net` and `site:archiveofourown.org` all
// produced a filter no row could satisfy — which reads as "the index has none
// of this" rather than "that was not understood".
const SITE_ALIASES: Record<string, string> = {
  "ao3": "ao3", "a03": "ao3", "archive": "ao3",
  "archiveofourown": "ao3", "archiveofourown.org": "ao3",
  "archive of our own": "ao3", "otw": "ao3",

  "ffnet": "ffnet", "ffn": "ffnet", "ff": "ffnet",
  "ff.net": "ffnet", "fanfiction": "ffnet", "fanfiction.net": "ffnet",
  "fanfictionnet": "ffnet", "www.fanfiction.net": "ffnet",

  "fictionalley": "fictionalley", "ficalley": "fictionalley",
  "fiction alley": "fictionalley", "fa": "fictionalley",
  "fictionalley.org": "fictionalley",
}

// Operators whose value is always one word. `language` is deliberately absent —
// "Bahasa Indonesia" is a real value, so it keeps the multi-word rule and
// quoting is how you bound it. Mirrors _SINGLE_TOKEN in query_parser.py.
const SINGLE_TOKEN = new Set([
  "sites", "ratings", "status", "word_count", "updated_after",
  "crossovers", "in_series",
])

export function canonicalSite(value: string): string | null {
  return SITE_ALIASES[value.toLowerCase().split(/\s+/).join(" ").trim()] ?? null
}

const RATING_ALIASES: Record<string, string> = {
  e: "E", explicit: "E", x: "E",
  m: "M", mature: "M",
  t: "T", teen: "T",
  g: "G", general: "G", all: "G",
  nr: "NR", none: "NR", unrated: "NR",
}

// Words that end a bare multi-word operator value, because they are shorthand
// belonging to the query itself. Mirrors _SHORTHAND_RE in query_parser.py.
// Not sticky and not global, so .test() has no lastIndex to carry between calls.
const SHORTHAND_RE =
  /^(?:complete|completed|wip|incomplete|ongoing|explicit|mature|teen|general|gen|[<>]=?[\d.]+[km]?\+?|[\d.]+[km]\+|[\d.]+[km]-[\d.]+[km])$/i

const STATUS_WORDS: Record<string, string> = {
  complete: "complete", completed: "complete",
  wip: "in_progress", incomplete: "in_progress",
  ongoing: "in_progress", in_progress: "in_progress",
}

const RATING_WORDS: Record<string, string> = {
  explicit: "E", mature: "M", teen: "T", general: "G",
  "not-rated": "NR", unrated: "NR",
}

function parseWordCount(val: string): [number | null, number | null] {
  val = val.toLowerCase().trim()
  const range = val.match(/^(\d+(?:\.\d+)?)(k|m)-(\d+(?:\.\d+)?)(k|m)$/)
  if (range) {
    const n = (v: string, u: string) => Math.round(parseFloat(v) * (u === "k" ? 1000 : 1_000_000))
    return [n(range[1], range[2]), n(range[3], range[4])]
  }
  const op = val.match(/^(>|<|>=|<=)(\d+(?:\.\d+)?)(k|m)\+?$/)
  if (op) {
    const n = Math.round(parseFloat(op[2]) * (op[3] === "k" ? 1000 : 1_000_000))
    return op[1].startsWith(">") ? [n, null] : [null, n]
  }
  const bare = val.match(/^(\d+(?:\.\d+)?)(k|m)\+?$/)
  if (bare) {
    return [Math.round(parseFloat(bare[1]) * (bare[2] === "k" ? 1000 : 1_000_000)), null]
  }
  return [null, null]
}

function parseDate(val: string): string | null {
  const rel = val.match(/^(\d+)(d|w|m|y)$/i)
  if (rel) {
    const n = parseInt(rel[1])
    const days: Record<string, number> = { d: n, w: n * 7, m: n * 30, y: n * 365 }
    const d = new Date()
    d.setDate(d.getDate() - (days[rel[2].toLowerCase()] ?? 0))
    return d.toISOString().split("T")[0]
  }
  if (/^20\d\d$/.test(val)) return `${val}-01-01`
  if (/^\d{4}-\d{2}-\d{2}$/.test(val)) return val
  return null
}

function isWordCountShorthand(word: string): boolean {
  const w = word.toLowerCase()
  return /^[><]=?[\d.]+[km]\+?$/.test(w) || /^[\d.]+[km]\+$/.test(w)
}

// Find all "key:" positions
const KEY_RE = /(-?)(\w+)\s*:\s*/gi

/** Does this operator value need quoting to survive a round trip through
 *  parseQuery?
 *
 *  The serialiser used to quote ANY value containing a space, which meant the
 *  bar filled up with `fandom:"Harry Potter" char:"Hermione Granger"` for the
 *  ordinary case of clicking two chips — noisy to read and worse to edit by
 *  hand, since a reader who deletes one quote silently changes what is searched.
 *
 *  A bare multi-word value is already fine: _take_value runs it to the next
 *  operator key. Quotes are only load-bearing in three cases, all checked here
 *  rather than guessed at the call site:
 *
 *    1. the value ENDS in a shorthand word, which the parser strips as belonging
 *       to the query ("complete", "wip", ">100k") — a fandom really called
 *       "... Complete" would lose its last word
 *    2. the value contains something that reads as `key:`, which would end the
 *       value early and start a second operator
 *    3. the value contains a double quote of its own
 */
export function needsQuoting(value: string): boolean {
  const v = value.trim()
  if (!v) return false
  if (v.includes('"')) return true

  const words = v.split(/\s+/)
  if (words.length > 1 && SHORTHAND_RE.test(words[words.length - 1])) return true

  // Any `word:` inside the value that the parser would recognise as an operator.
  const embedded = /(?:^|\s)-?(\w+)\s*:/g
  let m: RegExpExecArray | null
  while ((m = embedded.exec(v)) !== null) {
    if (FIELD_ALIASES[m[1].toLowerCase()]) return true
  }
  return false
}

/** Render an operator value for the search bar, quoting only when it matters. */
export function quoteValue(value: string): string {
  return needsQuoting(value) ? `"${value}"` : value
}


export function parseQuery(raw: string): ParsedQuery {
  const pq: ParsedQuery = {
    cleanText: "", fandoms: [], relationships: [], characters: [],
    tags: [],
    sections: [], ratings: [], warnings: [], categories: [], sites: [],
    excFandoms: [], excRelationships: [], excCharacters: [], excTags: [],
    status: null, language: null, author: null, wordCountMin: null, wordCountMax: null,
    updatedAfter: null, crossovers: null, inSeries: null, tokens: [],
  }

  if (!raw?.trim()) return pq

  KEY_RE.lastIndex = 0
  const keyPositions: Array<{ kstart: number; kend: number; exclude: boolean; canonical: string }> = []

  let m: RegExpExecArray | null
  while ((m = KEY_RE.exec(raw)) !== null) {
    const keyRaw = m[2].toLowerCase()
    const canonical = FIELD_ALIASES[keyRaw]
    if (canonical) {
      keyPositions.push({ kstart: m.index, kend: m.index + m[0].length, exclude: m[1] === "-", canonical })
    }
  }

  const consumed: [number, number][] = []
  let text = raw

  for (let i = 0; i < keyPositions.length; i++) {
    const { kstart, kend, exclude, canonical } = keyPositions[i]
    const nextKeyStart = i + 1 < keyPositions.length ? keyPositions[i + 1].kstart : raw.length
    const available = raw.slice(kend, nextKeyStart)

    let value: string
    let spanEnd: number

    if (available.startsWith('"')) {
      const endQ = available.indexOf('"', 1)
      if (endQ === -1) { value = available.slice(1).trim(); spanEnd = kend + available.length }
      else { value = available.slice(1, endQ).trim(); spanEnd = kend + endQ + 1 }
    } else if (SINGLE_TOKEN.has(canonical)) {
      // Exactly one word, so free text after the operator stays free text.
      // Without this `rating:M harry potter` took "M harry potter" as the
      // rating: no rating matched and no search text was left, so the bar
      // silently searched the whole index. Must match _SINGLE_TOKEN in
      // backend/query_parser.py, or a URL means one thing in the bar and
      // another when the API re-parses it.
      const first = available.trim().split(/\s+/)[0] ?? ""
      value = first
      spanEnd = first ? kend + available.indexOf(first) + first.length : kend
    } else {
      // A bare multi-word value, minus any trailing shorthand words — those
      // belong to the query, not to this value.
      //
      // The backend parser has always done this and this one never did, so the
      // README's headline syntax was broken in the actual search bar:
      // `fandom:Harry Potter complete >100k` set the fandom to the whole string
      // "Harry Potter complete >100k", which matches no facet, and set neither
      // the status nor the word count. The same string sent straight to the API
      // worked, so the bar and a hand-written URL disagreed.
      const words = available.trim().split(/\s+/).filter(Boolean)
      let keep = words.length
      // Never below one word: `status:complete` has a shorthand word as its
      // entire value, and stripping it would discard the operator.
      while (keep > 1 && SHORTHAND_RE.test(words[keep - 1])) keep--
      value = words.slice(0, keep).join(" ")
      // Consume up to the end of the last kept word, so trailing shorthand stays
      // in the free text where the shorthand pass below can find it.
      let idx = 0, consumed = 0
      for (const w of words.slice(0, keep)) {
        idx = available.indexOf(w, idx)
        consumed = idx + w.length
        idx = consumed
      }
      spanEnd = kend + consumed
    }

    if (!value) continue
    consumed.push([kstart, spanEnd])

    const tok: ParsedToken = { key: canonical, value, exclude, raw: raw.slice(kstart, spanEnd).trim() }

    if (canonical === "fandoms")       { exclude ? pq.excFandoms.push(value)       : pq.fandoms.push(value) }
    else if (canonical === "relationships") { exclude ? pq.excRelationships.push(value) : pq.relationships.push(value) }
    else if (canonical === "characters")   { exclude ? pq.excCharacters.push(value)    : pq.characters.push(value) }
    else if (canonical === "tags")         { exclude ? pq.excTags.push(value)           : pq.tags.push(value) }
    else if (canonical === "warnings")     { pq.warnings.push(value) }
    else if (canonical === "categories")   { pq.categories.push(value) }
    else if (canonical === "ratings") {
      const mapped = RATING_ALIASES[value.toLowerCase()]
      if (mapped) { pq.ratings.push(mapped); tok.value = mapped }
    }
    else if (canonical === "status")  { pq.status = STATUS_WORDS[value.toLowerCase()] ?? value.toLowerCase(); tok.value = pq.status! }
    else if (canonical === "word_count") {
      const [mn, mx] = parseWordCount(value)
      if (mn !== null) pq.wordCountMin = mn
      if (mx !== null) pq.wordCountMax = mx
    }
    else if (canonical === "updated_after") { const d = parseDate(value); if (d) pq.updatedAfter = d }
    else if (canonical === "language")  { pq.language = value }
    else if (canonical === "author")    { pq.author = value }
    // An unrecognised archive drops the filter rather than keeping one nothing
    // can match. The span is already consumed, so the words do not leak into
    // the free-text query either.
    else if (canonical === "sites")     {
      const s = canonicalSite(value)
      if (!s) continue
      pq.sites.push(s)
      tok.value = s
    }
    else if (canonical === "crossovers") {
      const v = value.toLowerCase()
      pq.crossovers = ["only","yes","true"].includes(v) ? "only" : ["no","false","exclude"].includes(v) ? "exclude" : "include"
    }
    else if (canonical === "in_series") {
      const v = value.toLowerCase()
      if (["true", "yes", "in", "series"].includes(v)) {
        pq.inSeries = true; tok.value = "true"
      } else if (["false", "no", "standalone", "alone", "oneshot", "one-shot"].includes(v)) {
        pq.inSeries = false; tok.value = "false"
      }
    }

    pq.tokens.push(tok)
  }

  // Strip consumed
  for (const [s, e] of [...consumed].sort((a, b) => b[0] - a[0])) {
    text = text.slice(0, s) + text.slice(e)
  }

  // Standalone shorthands
  const remaining: string[] = []
  for (const word of text.split(/\s+/)) {
    const wl = word.toLowerCase().replace(/[.,]+$/, "")
    if (!wl) continue
    if (isWordCountShorthand(wl)) {
      const [mn, mx] = parseWordCount(wl)
      if (mn !== null) pq.wordCountMin = mn
      if (mx !== null) pq.wordCountMax = mx
      pq.tokens.push({ key: "word_count", value: word, exclude: false, raw: word })
    } else if (STATUS_WORDS[wl]) {
      pq.status = STATUS_WORDS[wl]
      pq.tokens.push({ key: "status", value: pq.status, exclude: false, raw: word })
    } else if (RATING_WORDS[wl]) {
      pq.ratings.push(RATING_WORDS[wl])
      pq.tokens.push({ key: "ratings", value: RATING_WORDS[wl], exclude: false, raw: word })
    } else {
      remaining.push(word)
    }
  }

  pq.cleanText = remaining.join(" ").trim()
  return pq
}

export function parsedToSearchParams(pq: ParsedQuery): Record<string, any> {
  const csv = (arr: string[]) => arr.length ? arr.join(",") : undefined
  return {
    q: pq.cleanText || undefined,
    sites: csv(pq.sites),
    fandoms: csv(pq.fandoms),
    relationships: csv(pq.relationships),
    characters: csv(pq.characters),
    tags: csv(pq.tags),
    sections: csv(pq.sections),
    ratings: csv(pq.ratings),
    warnings: csv(pq.warnings),
    categories: csv(pq.categories),
    exclude_fandoms: csv(pq.excFandoms),
    exclude_relationships: csv(pq.excRelationships),
    exclude_characters: csv(pq.excCharacters),
    exclude_tags: csv(pq.excTags),
    status: pq.status ?? undefined,
    language: pq.language ?? undefined,
    author: pq.author ?? undefined,
    word_count_min: pq.wordCountMin ?? undefined,
    word_count_max: pq.wordCountMax ?? undefined,
    updated_after: pq.updatedAfter ?? undefined,
    crossovers: pq.crossovers ?? undefined,
    in_series: pq.inSeries ?? undefined,
  }
}
