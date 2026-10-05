// Where a reader should pick a story back up.
//
// A progress entry records `chapter` — the chapter most recently OPENED — and
// `positions`, how far into each chapter the reader has scrolled, merged across
// every device (backend/api/userdata.py unions the map). Resuming from
// `chapter` alone sent a reader back from chapter 61 to 22, measured on a real
// account: a tab left open on chapter 22 re-saved "chapter 22, now" when it was
// next shown, and the newest timestamp wins the sync. Nothing was lost — the
// positions still ran to 61 — but the resume button pointed at the stale tab.
//
// So the resume point is the furthest chapter the reader has actually scrolled
// into, which no stale tab or device can move backwards, and the last-opened
// chapter is offered beside it when the two disagree, for a deliberate re-read.

export interface ProgressEntry {
  chapter?: number
  scrollPct?: number
  positions?: Record<string, number>
  totalChapters?: number
  at?: string
}

export interface Resume {
  chapter: number        // where "Continue" goes
  pct: number            // how far into that chapter
  lastOpened: number | null  // a different, more recently opened chapter, if any
}

export function resumePoint(e: ProgressEntry | null | undefined): Resume | null {
  if (!e) return null
  const pos = e.positions || {}
  let furthest = 0
  for (const [k, v] of Object.entries(pos)) {
    const n = Number(k)
    if (Number.isFinite(n) && typeof v === "number" && v > 0 && n > furthest) furthest = n
  }
  const opened = e.chapter && e.chapter > 0 ? e.chapter : 0
  const chapter = Math.max(furthest, opened)
  if (!chapter) return null
  return {
    chapter,
    pct: typeof pos[chapter] === "number" ? pos[chapter] : (chapter === opened ? e.scrollPct ?? 0 : 0),
    lastOpened: opened && opened !== chapter ? opened : null,
  }
}
