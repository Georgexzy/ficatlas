/**
 * The loading compass: an instrument, not the logo.
 *
 * Every loader on the site used to be the logo (CompassMark) with its needle
 * animated. That was iterated on half a dozen times — needle wider, narrower,
 * shorter, re-shaded — and it kept "looking a bit off", because the problem was
 * not any one dimension: the logo is an eight-point star, and its needle is a
 * second star-shaped kite laid over it. Mid-swing the two overlap into one
 * jumbled starburst, and with no dial behind them nothing says "compass" — it
 * reads as a twinkle.
 *
 * So the loader is drawn as the thing it is pretending to be: a fixed FACE (a
 * bezel ring, degree ticks, the cardinals marked longer, an N) and a slim
 * needle on a hub turning over it. The face gives the motion something to be
 * measured against, which is what makes a seek-and-settle read as a needle
 * finding north rather than an icon wobbling. The logo stays the logo.
 *
 * `mini` is the same instrument for small sizes (the search button, inline
 * spinners): only the ring, four cardinal ticks and the needle, all in
 * currentColor, because at 20px degree ticks are noise and the button's gold
 * background swallows any gold drawn on it.
 *
 * Colours come from the theme tokens in CSS, so light and dark need nothing
 * here. aria-hidden: every caller puts a text label beside it.
 */
const TICKS = Array.from({ length: 36 }, (_, i) => i * 10)
// Two decimals. Node and the browser disagree in the last digits of cos/sin
// (9.784711149549633 vs …636), so unrounded coordinates made every search page
// report a hydration mismatch.
const r = (n: number) => Math.round(n * 100) / 100

export default function CompassDial({ mini = false, className = "" }: {
  mini?: boolean; className?: string
}) {
  return (
    <svg className={`dial ${mini ? "dial--mini" : ""} ${className}`} viewBox="0 0 64 64"
         aria-hidden="true" focusable="false">
      <circle className="dial__bezel" cx="32" cy="32" r="30" />
      {!mini && <circle className="dial__face" cx="32" cy="32" r="26.5" />}
      {(mini ? [0, 90, 180, 270] : TICKS).map(deg => {
        const cardinal = deg % 90 === 0
        // Ticks hang inward from the bezel; cardinals reach further in.
        const r1 = mini ? 30 : 29
        const r2 = mini ? 25.5 : cardinal ? 23.5 : 26.5
        const a = (deg - 90) * Math.PI / 180
        return (
          <line key={deg}
            className={`dial__tick ${cardinal ? "dial__tick--cardinal" : ""}`}
            x1={r(32 + r1 * Math.cos(a))} y1={r(32 + r1 * Math.sin(a))}
            x2={r(32 + r2 * Math.cos(a))} y2={r(32 + r2 * Math.sin(a))} />
        )
      })}
      {/* North, marked ON the bezel rather than as an "N" inside the face: the
          needle rests pointing at it, and a letter under the tip was covered
          by the very thing it labels. */}
      {!mini && <polygon className="dial__north" points="32,0.6 35.2,5.6 28.8,5.6" />}
      <g className="dial__needle">
        {/* North half lit, south half shaded: the one thing a needle has to
            say is which end is north. Slim and long, so it never reads as a
            second rose. */}
        <polygon className="dial__needle-n" points={mini ? "32,9 35.2,32 28.8,32" : "32,11 34.4,32 29.6,32"} />
        <polygon className="dial__needle-s" points={mini ? "32,55 28.8,32 35.2,32" : "32,53 29.6,32 34.4,32"} />
        <circle className="dial__hub" cx="32" cy="32" r={mini ? 3.2 : 2.6} />
      </g>
    </svg>
  )
}
