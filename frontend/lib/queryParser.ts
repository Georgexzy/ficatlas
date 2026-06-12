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
  warn: "warnings", warning: "warnings", warnings: "warnings",
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

export function parseQuery(raw: string): ParsedQuery {
  const pq: ParsedQuery = {
    cleanText: "", fandoms: [], relationships: [], characters: [],
    tags: [], ratings: [], warnings: [], categories: [], sites: [],
    excFandoms: [], excRelationships: [], excCharacters: [], excTags: [],
    status: null, language: null, wordCountMin: null, wordCountMax: null,
    updatedAfter: null, crossovers: null, tokens: [],
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
    } else {
      value = available.trim()
      spanEnd = nextKeyStart
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
    else if (canonical === "sites")     { pq.sites.push(value.toLowerCase()) }
    else if (canonical === "crossovers") {
      const v = value.toLowerCase()
      pq.crossovers = ["only","yes","true"].includes(v) ? "only" : ["no","false","exclude"].includes(v) ? "exclude" : "include"
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
    ratings: csv(pq.ratings),
    warnings: csv(pq.warnings),
    categories: csv(pq.categories),
    exclude_fandoms: csv(pq.excFandoms),
    exclude_relationships: csv(pq.excRelationships),
    exclude_characters: csv(pq.excCharacters),
    exclude_tags: csv(pq.excTags),
    status: pq.status ?? undefined,
    language: pq.language ?? undefined,
    word_count_min: pq.wordCountMin ?? undefined,
    word_count_max: pq.wordCountMax ?? undefined,
    updated_after: pq.updatedAfter ?? undefined,
    crossovers: pq.crossovers ?? undefined,
  }
}
