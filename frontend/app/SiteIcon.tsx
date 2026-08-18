/**
 * The archive a result came from, as a mark rather than only a word.
 *
 * Every card already carried a coloured text badge ("AO3", "FF.net",
 * "FictionAlley"), and colour alone is not a label: the three badges differ by
 * hue, which is the one channel a red-green colourblind reader cannot rely on
 * and which disappears entirely in a high-contrast theme. A shape carries the
 * same information through both.
 *
 * Deliberately inline SVG rather than an image or an icon font:
 *
 *   * it inherits `currentColor`, so each badge's existing per-site colour
 *     paints the glyph too and light/dark themes need no extra rules
 *   * no network request, which matters on a card list that can render 100 of
 *     these in one page
 *   * a strict CSP is in force (see next.config.ts) and an external sprite would
 *     be one more origin to allow
 *
 * The marks are silhouettes, not logos. They have to read at 11px, and copying
 * AO3's or FanFiction.net's actual branding onto a third-party index would be
 * passing off someone else's mark as ours.
 */

const PATHS: Record<string, string> = {
  // A bookmark ribbon — the shape of a saved work.
  ao3: "M4 1.5h8a1 1 0 0 1 1 1v12l-5-3.6-5 3.6v-12a1 1 0 0 1 1-1z",
  // An open book, spine in the middle.
  ffnet: "M1 3.2c2.2-.7 4.4-.7 6.5.6v9.6C5.4 12.1 3.2 12.1 1 12.8V3.2zm14 0v9.6c-2.2-.7-4.4-.7-6.5.6V3.8C10.6 2.5 12.8 2.5 15 3.2z",
  // A scroll — a dead archive preserved as a document.
  fictionalley: "M3 1.5h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V3.5a2 2 0 0 1 1-1.99zM5 5h6v1.4H5V5zm0 3.3h6v1.4H5V8.3zm0 3.3h4V13H5v-1.4z",
}

export default function SiteIcon({ site }: { site: string }) {
  const d = PATHS[site]
  // An archive we have no mark for renders nothing rather than a placeholder —
  // the text label beside it already says which one it is.
  if (!d) return null
  return (
    // aria-hidden with no <title>: this sits immediately beside the archive's
    // NAME in the same badge, so announcing it again would make every result
    // read its source twice.
    <svg className="site-icon" viewBox="0 0 16 16" width="1em" height="1em"
         fill="currentColor" aria-hidden="true" focusable="false">
      <path d={d} />
    </svg>
  )
}
