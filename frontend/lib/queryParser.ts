/**
 * FicAtlas client-side query parser
 * Mirrors backend query_parser.py — parses the search bar in real time
 * so the sidebar filters can reflect what's been typed.
 */

export interface ParsedToken {
  key: string
  value: string
  exclude: boolean
  raw: string
}

export interface ParsedQuery {
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
  wordCountMin: number | null
  wordCountMax: number | null
  updatedAfter: string | null
  crossovers: string | null
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
  updated: "updated_after", update: "updated_after", since: "updated_after",
  site: "sites",
  crossover: "crossovers", xover: "crossovers",
  warn: "warnings", warning: "warnings",
  category: "categories", cat: "categories",
}

const RATING_ALIASES: Record<string, string> = {
  e: "E", explicit: "E", x: "E",
  m: "M", mature: "M",
  t: "T", teen: "T",
  g: "G", general: "G", all: "G",
  nr: "NR", none: "NR", unrated: "NR",
}

const STATUS_WORDS: Record<string, string> = {
  complete: "complete", completed: "complete",
  wip: "in_progress", incomplete: "in_progress", ongoing: "in_progress",
}

const RATING_WORDS: Record<string, string> = {
  explicit: "E", mature: "M", teen: "T", general: "G",
  "not-rated": "NR", unrated: "NR",
}

function parseWordCount(val: string): [number | null, number | null] {
  val = val.toLowerCase().trim()
  // range: 100k-200k
  const range = val.match(/^(\d+(?:\.\d+)?)(k|m)-(\d+(?:\.\d+)?)(k|m)$/)
  if (range) {
    const toN = (v: string, u: string) => Math.round(parseFloat(v) * (u === "k" ? 1000 : 1_000_000))
    return [toN(range[1], range[2]), toN(range[3], range[4])]
  }
  // operator: >100k
  const op = val.match(/^(>|<|>=|<=)(\d+(?:\.\d+)?)(k|m)\+?$/)
  if (op) {
    const n = Math.round(parseFloat(op[2]) * (op[3] === "k" ? 1000 : 1_000_000))
    if (op[1] === ">" || op[1] === ">=") return [n, null]
    return [null, n]
  }
  // bare: 100k or 100k+
  const bare = val.match(/^(\d+(?:\.\d+)?)(k|m)\+?$/)
  if (bare) {
    const n = Math.round(parseFloat(bare[1]) * (bare[2] === "k" ? 1000 : 1_000_000))
    return [n, null]
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

const OPERATOR_RE = /(-?)(\w+):(?:"([^"]+)"|(\S+))/gi

export function parseQuery(raw: string): ParsedQuery {
  const pq: ParsedQuery = {
    cleanText: "", fandoms: [], relationships: [], characters: [],
    tags: [], ratings: [], warnings: [], categories: [], sites: [],
    excFandoms: [], excRelationships: [], excCharacters: [], excTags: [],
    status: null, language: null, wordCountMin: null, wordCountMax: null,
    updatedAfter: null, crossovers: null, tokens: [],
  }

  let text = raw
  const consumedSpans: [number, number][] = []

  // Reset regex
  OPERATOR_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = OPERATOR_RE.exec(raw)) !== null) {
    const excl = match[1] === "-"
    const keyRaw = match[2].toLowerCase()
    const value = match[3] ?? match[4]
    const canonical = FIELD_ALIASES[keyRaw]
    if (!canonical) continue

    consumedSpans.push([match.index, match.index + match[0].length])
    const token: ParsedToken = { key: canonical, value, exclude: excl, raw: match[0] }

    if (canonical === "fandoms")       { excl ? pq.excFandoms.push(value)       : pq.fandoms.push(value) }
    else if (canonical === "relationships") { excl ? pq.excRelationships.push(value) : pq.relationships.push(value) }
    else if (canonical === "characters")   { excl ? pq.excCharacters.push(value)    : pq.characters.push(value) }
    else if (canonical === "tags")         { excl ? pq.excTags.push(value)           : pq.tags.push(value) }
    else if (canonical === "warnings")     { pq.warnings.push(value) }
    else if (canonical === "categories")   { pq.categories.push(value) }
    else if (canonical === "ratings") {
      const mapped = RATING_ALIASES[value.toLowerCase()]
      if (mapped) { pq.ratings.push(mapped); token.value = mapped }
    }
    else if (canonical === "status")  { pq.status = STATUS_WORDS[value.toLowerCase()] ?? value.toLowerCase(); token.value = pq.status }
    else if (canonical === "word_count") {
      const [mn, mx] = parseWordCount(value)
      if (mn !== null) pq.wordCountMin = mn
      if (mx !== null) pq.wordCountMax = mx
    }
    else if (canonical === "updated_after") { const d = parseDate(value); if (d) pq.updatedAfter = d }
    else if (canonical === "language")  { pq.language = value }
    else if (canonical === "sites")     { pq.sites.push(value.toLowerCase()) }
    else if (canonical === "crossovers") {
      const v = value.toLowerCase()
      pq.crossovers = ["only","yes","true"].includes(v) ? "only" : ["no","false","exclude"].includes(v) ? "exclude" : "include"
    }

    pq.tokens.push(token)
  }

  // Strip consumed spans
  for (const [s, e] of [...consumedSpans].sort((a, b) => b[0] - a[0])) {
    text = text.slice(0, s) + text.slice(e)
  }

  // Standalone shorthands
  const remaining: string[] = []
  for (const word of text.split(/\s+/)) {
    const wl = word.toLowerCase().replace(/[.,]+$/, "")
    if (!wl) continue

    if (/^[><]=?[\d.]+[km]\+?$/.test(wl) || /^[\d.]+[km]\+$/.test(wl)) {
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

/** Convert ParsedQuery back to SearchParams for the API call */
export function parsedToSearchParams(pq: ParsedQuery): Record<string, any> {
  const csv = (arr: string[]) => arr.length ? arr.join(",") : undefined
  return {
    q:                     pq.cleanText || undefined,
    sites:                 csv(pq.sites),
    fandoms:               csv(pq.fandoms),
    relationships:         csv(pq.relationships),
    characters:            csv(pq.characters),
    tags:                  csv(pq.tags),
    ratings:               csv(pq.ratings),
    warnings:              csv(pq.warnings),
    categories:            csv(pq.categories),
    exclude_fandoms:       csv(pq.excFandoms),
    exclude_relationships: csv(pq.excRelationships),
    exclude_characters:    csv(pq.excCharacters),
    exclude_tags:          csv(pq.excTags),
    status:                pq.status ?? undefined,
    language:              pq.language ?? undefined,
    word_count_min:        pq.wordCountMin ?? undefined,
    word_count_max:        pq.wordCountMax ?? undefined,
    updated_after:         pq.updatedAfter ?? undefined,
    crossovers:            pq.crossovers ?? undefined,
  }
}
