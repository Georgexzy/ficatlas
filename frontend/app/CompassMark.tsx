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
 * THE NEEDLE is part of the mark, not an optional extra, and it is the first
 * thing here that took a measurement to get right. A compass is a FIXED rose
 * with something turning over it; rotating the whole <svg> instead gives a
 * slow, floral spinner, which is the generic thing this mark exists to stop
 * looking like. So the needle is a separate group, drawn last, and it is the
 * only part that moves.
 *
 * The needle has been resized twice, and both times the measurement pointed
 * somewhere other than where the complaint did. The first attempt was 2.3 wide
 * on this 32-unit grid, and the rose's own vertical cardinal point is 2.32 wide
 * and reaches further — so the needle was a strict SUBSET of a shape already in
 * the mark, on the same axis and in the same two colours. It animated, and
 * nothing about it was distinguishable from the point it was hiding inside.
 * Pixel-diffing the icon with and without it measured 1.3% of the tile.
 *
 * The fix was width: 4.2, comfortably past the cardinal. That worked and it was
 * wrong, because 8.4 units across a 32-unit grid is a quarter of the mark, and
 * the rose's horizontal points are only 2.32 wide where they meet the centre.
 * The needle buried them, the eight-point rose stopped reading as a rose, and
 * the logo went "fat and squat" — a chunky lozenge with two small side points.
 * Wide was solving separability by brute force and charging for it in the mark.
 *
 * What separability actually needs is not width but DISJOINTNESS. The needle now
 * stops 2.7 units short of the rose's tips at both ends, so a gap separates it
 * from everything it crosses, and it can go back to being slim (2.78) without
 * becoming a highlight of the cardinal. Both facts are in the polygon comment
 * below and the numbers live in tools/make-icons.py, which is what keeps the tab
 * icon and this the same picture.
 */
export default function CompassMark({ className = "" }: { className?: string }) {
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
      {/* The needle: a kite over the rose, lit at the north end and shaded at the
          south, because a needle's job is to say which way is north — the rose's
          left/right facet split says nothing about that. Same 32-unit grid and
          centre as the rose, so `transform-origin: 50%` with
          `transform-box: view-box` is the pivot with no arithmetic.

          The coordinates are NEEDLE_LEN 0.82 / NEEDLE_HALF 0.20 of a radius-15
          rose — tip at 3.7 and 28.3, half-width 3.0 — emitted from
          tools/make-icons.py. Two things are deliberate and both were measured
          rather than chosen by eye:

          It is NARROW, because the first one at half-width 4.2 was 8.4 units
          across on a 32-unit grid. That buried the rose's horizontal cardinal
          points, which are 2.32 wide where they meet the centre, so the
          eight-point rose stopped reading as a rose and the mark read as a fat
          lozenge with two small side points.

          And it is SHORT of the rose's tips, stopping 2.7 units short at both
          ends. That gap is what makes it an object turning over a fixed rose,
          so the needle no longer has to be wide to be separable — an earlier
          iteration solved separability with width alone and lost the rose.

          What carries the differentiation is the shading, not the width: a gold
          needle on a gold rose is invisible at any size, which is why the south
          half is filled at 45% opacity in the CSS. */}
      <g className="compass__needle">
        <polygon className="compass__needle-n" points="16,3.7 19,16 13,16" />
        <polygon className="compass__needle-s" points="16,28.3 13,16 19,16" />
      </g>
    </svg>
  )
}
