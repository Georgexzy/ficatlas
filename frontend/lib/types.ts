// Types mirroring the FastAPI response models

export type Site = "ao3" | "ffnet" | "fictionalley" | "wattpad" | "royalroad" | "spacebattles"
export type Rating = "G" | "T" | "M" | "E" | "NR"
export type Status = "complete" | "in_progress" | "abandoned" | "unknown"
export type Category = "F/F" | "F/M" | "M/M" | "Gen" | "Other" | "Multi"
export type CrossoverFilter = "include" | "exclude" | "only"

export interface StoryCard {
  id: string
  site: Site
  url: string
  title: string
  author: string
  author_url?: string
  summary?: string
  language: string
  rating?: Rating
  status: Status
  word_count: number
  chapter_count: number
  chapter_count_total?: number
  kudos: number
  hits: number
  bookmarks: number
  comments: number
  fandoms: string[]
  relationships: string[]
  characters: string[]
  tags: string[]
  warnings: string[]
  categories: string[]
  genres: string[]
  published_at?: string
  updated_at?: string
  is_live?: boolean
  /** The author verified control of their archive account and asked us to host
   *  their work. See backend/api/permissions.py. */
  author_verified?: boolean
  is_hosted?: boolean
  /** Only ever true for an admin: the API removes these rows for everyone else. */
  delisted?: boolean
  /** Set when this work belongs to a series; attached per results page. */
  series_name?: string | null
  series_id?: string | null
  series_position?: number | null
  /** FictionAlley section: Schnoogle, The Dark Arts, Astronomy Tower, Riddikulus. */
  archive_section?: string | null
  cross_post_urls?: string[]
  /** Which bulk import this row came from (not a content tag). */
  sources?: string[]
}

export interface SearchResponse {
  total: number
  count_is_capped?: boolean
  page: number
  per_page: number
  results: StoryCard[]
  sites_searched: string[]
  /** Matches per archive, e.g. {ao3: 124, ffnet: 63}. Present only when the
   *  total is exact and more than one archive was searched — see the note on
   *  site_counts in backend/api/search.py for why it is withheld otherwise. */
  site_counts?: Record<string, number>
  live_count?: number
  /** How many matches the explicit-rating filter is hiding. Counted by the
   *  server only when the page came back EMPTY, so it is 0 on any search that
   *  returned results — it exists to turn "No stories matched" into "your
   *  content setting hid all of them". Capped at 999, so treat a value of 999
   *  as "999+" rather than exact. */
  hidden_explicit?: number
  parsed_tokens?: any[]
  /** Spelling rescues, sent only when the search matched nothing at all. */
  suggestions?: Suggestion[]
}

/** Something the reader might have meant, when they matched nothing. */
export interface Suggestion {
  kind: string      // fandom | character | relationship | tag
  value: string     // the canonical spelling, as the archives write it
  count: number     // how many works carry it
  query: string     // a ready-made search that runs it
  /**
   * Why it is offered:
   *   spelling — a facet the reader may have meant (the original feature)
   *   relax    — their own query minus the term that is costing the results
   *   broaden  — a narrow tag swapped for the way the archives usually spell it
   *   split    — a rarely-filed pairing swapped for the two characters in it
   *
   * The last three exist because a measurement said the first was answering
   * the wrong question: ablating every component of every query built from a
   * corpus of real fic-finder posts, dropping one TAG recovers a median 3,829
   * works while dropping the fandom recovers 1. A typo announces itself;
   * over-constraint looks exactly like a thin index.
   */
  reason?: "spelling" | "relax" | "broaden" | "split"
  /** Works the suggested query would find — probe-measured and capped, so a
   *  floor. `count` is how many carry the term ANYWHERE, which on an
   *  over-constrained search is a different question. */
  works?: number | null
  /** For relax/broaden/split: the term being removed or replaced. */
  drops?: string | null
}

export interface SearchParams {
  /** Only works on a community recommendation list. See _ANY_RECS_MARKER. */
  recs_only?: boolean
  /** Include works flagged for underage content. Separate from `explicit` on
   *  purpose: that one is about taste, this one is about what a shared link
   *  shows a stranger. Nothing that generates a link ever sets it. */
  include_underage?: boolean
  q?: string
  sites?: string             // "ao3,ffnet"
  /** FictionAlley sections, comma-separated. */
  sections?: string
  // Include
  fandoms?: string
  characters?: string
  relationships?: string
  tags?: string
  ratings?: string
  warnings?: string
  categories?: string
  crossovers?: CrossoverFilter
  // Exclude
  exclude_fandoms?: string
  exclude_characters?: string
  exclude_relationships?: string
  exclude_tags?: string
  exclude_ratings?: string
  exclude_warnings?: string
  exclude_categories?: string
  // More options
  status?: string
  language?: string
  word_count_min?: number
  word_count_max?: number
  updated_after?: string
  updated_before?: string
  published_after?: string
  explicit?: boolean
  /** Exact author match — every work by one person, across all archives. */
  author?: string
  /** How multiple values in one filter combine: "all" (AND) or "any" (OR). */
  match_mode?: "all" | "any"
  /** Also return stories that have no data at all for a filtered field. */
  include_unknown?: boolean
  /** Minimum DarkLordPotter community star rating (0-5). */
  dlp_min_rating?: number
  /** Restrict to works that are (true) or are not (false) part of a series. */
  in_series?: boolean
  /**
   * Let a work satisfy `word_count_min` on the strength of its SERIES total,
   * when the series has more than one work. An ADD-ON: it widens the length
   * filter and never restricts results to works that are in a series.
   */
  count_series?: boolean
  search_within?: string
  // Pagination
  sort?: string
  page?: number
  per_page?: number
  live?: boolean
}