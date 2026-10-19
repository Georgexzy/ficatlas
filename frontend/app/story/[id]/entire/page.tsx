"use client"

// The entire-work reader — AO3's "Entire Work" — as its own URL, so the view
// can be linked, bookmarked and passed on exactly like a chapter page.
//
// It renders the SAME component as the chapter reader rather than a second one.
// Typography, the settings sheet, progress, offline storage, error screens and
// keyboard handling all live there, and a reader who chose serif at 19px on
// their phone has chosen it for this view too — a sibling component would
// inherit none of that and would drift from it, which is how two readers of the
// same story end up looking like two different sites.
//
// The view is told apart by the URL itself, not by a prop: this route has no
// chapter number in it and the chapter route always does, and Next.js
// constrains each route file's default export to its own `PageProps`, which
// leaves a page component room for the framework's props or none at all. Both
// are documented on `ChapterPage`.
import ChapterReader from "../chapter/[num]/page"

export default function EntireWorkPage() {
  return <ChapterReader />
}
