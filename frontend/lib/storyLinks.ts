// Where a story's "open it" button should actually point.
//
// Two site quirks make this non-trivial, and both were previously duplicated
// between the results grid and the story detail page:
//
//   fictionalley — the site is defunct, so links go to Wayback. Snapshots were
//     crawled with an explicit :80 port, which Wayback needs in the URL to match.
//
//   seed://      — metadata-only rows (the janelleshane HP seed) have no real
//     page. Linking straight at the synthetic URL gave a dead "Open on AO3"
//     button. These rows exist to tell you a fic EXISTS, so send the reader to an
//     AO3 search for the title and author instead, which is the useful next step.
//
//   ficatlas://  — a work uploaded here as an EPUB. There is no external page at
//     all: this reader IS the source. It was falling through to the default and
//     rendering "Read on AO3 ↗" pointing at `ficatlas://upload/<uuid>`, which no
//     browser can open — a dead button sitting next to the working "Read here"
//     on the owner's own upload.

export type StoryLinkTarget = {
  href: string
  label: string
  /** True when this points at a search rather than the work itself. */
  isSearch: boolean
  /** True when there is NO external source and callers should render no link. */
  isInternal?: boolean
}

/** An EPUB uploaded here. The reader is the source; there is nowhere else to go. */
export function isUploadUrl(url: string | undefined | null): boolean {
  return !!url && url.startsWith("ficatlas://")
}

export function isSeedUrl(url: string | undefined | null): boolean {
  return !!url && url.startsWith("seed://")
}

/** AO3 work-search for a title/author we only know by name. */
export function ao3SearchUrl(title: string, author?: string | null): string {
  const qs = new URLSearchParams()
  qs.set("work_search[query]", [title, author].filter(Boolean).join(" "))
  return `https://archiveofourown.org/works/search?${qs.toString()}`
}

export function storyLink(
  story: { url: string; site: string; title: string; author?: string | null },
  siteLabels: Record<string, string> = {},
): StoryLinkTarget {
  if (isUploadUrl(story.url)) {
    // href is the local page rather than the unopenable scheme, so a caller that
    // ignores isInternal still degrades to something that works.
    return {
      href: `/story/${(story as { id?: string }).id ?? ""}`,
      label: "Uploaded here",
      isSearch: false,
      isInternal: true,
    }
  }

  if (isSeedUrl(story.url)) {
    return {
      href: ao3SearchUrl(story.title, story.author),
      label: "Find on AO3 ↗",
      isSearch: true,
    }
  }

  if (story.site === "fictionalley") {
    let u = story.url
    if (u.includes("fictionalley.org") && !u.includes("fictionalley.org:")) {
      u = u.replace("fictionalley.org/", "fictionalley.org:80/")
    }
    return {
      href: `https://web.archive.org/web/2010/${u}`,
      label: "Read on Wayback ↗",
      isSearch: false,
    }
  }

  // "Read on AO3", not "Open on AO3". The button is the primary action on most
  // result cards, and it should name what the reader is about to do rather than
  // describe a mechanism. "Find on AO3" is kept above for seed rows precisely
  // because those go to a search, not to the work — the two must not read alike.
  return {
    href: story.url,
    label: `Read on ${siteLabels[story.site] ?? story.site} ↗`,
    isSearch: false,
  }
}
