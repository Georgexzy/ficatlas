/**
 * The FicAtlas compass rose, beside the wordmark.
 *
 * Same shape as the app icon, and not by eye: the polygon coordinates below are
 * emitted from the same construction as `_rose()` in frontend/tools/make-icons.py
 * — one kite per point, built from a direction vector and its perpendicular, on
 * a 32-unit grid. If the mark changes, change it there and re-emit these, or the
 * tab icon and the header will quietly stop being the same logo, which is the
 * exact drift this whole exercise started with.
 *
 * Inline SVG rather than an <img>, for the three reasons app/SiteIcon.tsx
 * already gives: it takes its colours from CSS so light and dark need no extra
 * rules, it costs no request on a page that may already be fetching a hundred
 * things, and a strict CSP is in force so an external asset would be one more
 * origin to allow.
 *
 * Two fills, not `currentColor`. The rose is the wordmark's own two colours —
 * gold and cream, the same split as `Fic<em>Atlas</em>` — and that pairing is
 * what makes it read as this site's mark rather than a generic star. They come
 * from the theme tokens, so the cream half follows the text colour into the
 * light theme instead of vanishing into a parchment background.
 *
 * aria-hidden: it sits inside the link that already says "FicAtlas home", so
 * announcing it again would make every page start by saying the name twice.
 *
 * `needle` is opt-in and off by default. The rose alone is the logo, and two of
 * its three call sites are that: the wordmark, and the button that spins while
 * a search runs. The search results loader is the one place that wants the whole
 * instrument, because a compass is a FIXED rose with something turning over it
 * — rotating all eight points instead just gives a slow, floral spinner, which
 * is the generic thing this mark is supposed to stop looking like. So the needle
 * is a separate group, drawn last so it sits over the rose, and it is the only
 * part that moves.
 */
export default function CompassMark({
  className = "",
  needle = false,
}: { className?: string; needle?: boolean }) {
  return (
    <svg className={`compass ${className}`} viewBox="0 0 32 32"
         width="1em" height="1em" aria-hidden="true" focusable="false">
      {/* Intercardinals first so the cardinal points sit on top of them, which
          is the order a drawn rose reads in. */}
      <g className="compass__gold">
        <polygon points="21.52,10.48 16.9,16.9 16,16" />
        <polygon points="21.52,21.52 15.1,16.9 16,16" />
        <polygon points="10.48,21.52 15.1,15.1 16,16" />
        <polygon points="10.48,10.48 16.9,15.1 16,16" />
        <polygon points="16,1 18.32,16 16,16" />
        <polygon points="31,16 16,18.32 16,16" />
        <polygon points="16,31 13.68,16 16,16" />
        <polygon points="1,16 16,13.68 16,16" />
      </g>
      <g className="compass__cream">
        <polygon points="21.52,10.48 15.1,15.1 16,16" />
        <polygon points="21.52,21.52 16.9,15.1 16,16" />
        <polygon points="10.48,21.52 16.9,16.9 16,16" />
        <polygon points="10.48,10.48 15.1,16.9 16,16" />
        <polygon points="16,1 13.68,16 16,16" />
        <polygon points="31,16 16,13.68 16,16" />
        <polygon points="16,31 18.32,16 16,16" />
        <polygon points="1,16 16,18.32 16,16" />
      </g>
      {/* The needle: one long kite through the centre, split into a lit half and
          a shaded one so the direction of travel stays readable while it turns.
          Same 32-unit grid and same centre as the rose, so `transform-origin:
          50%` on the group (with transform-box: view-box) is the pivot with no
          arithmetic. Deliberately NOT emitted from make-icons.py: that builds
          the logo, and this is an instrument. */}
      {needle && (
        <g className="compass__needle">
          <polygon className="compass__needle-n" points="16,2.4 18.3,16 13.7,16" />
          <polygon className="compass__needle-s" points="16,29.6 13.7,16 18.3,16" />
        </g>
      )}
    </svg>
  )
}
