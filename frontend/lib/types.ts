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
  is_hosted?: boolean
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
  live_count?: number
  parsed_tokens?: any[]
}

export interface SearchParams {
  q?: string
  sites?: string             // "ao3,ffnet"
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
  search_within?: string
  // Pagination
  sort?: string
  page?: number
  per_page?: number
  live?: boolean
}