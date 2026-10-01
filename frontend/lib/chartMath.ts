/**
 * Chart geometry, as pure functions.
 *
 * No DOM and no React, so the arithmetic that decides where a line goes, which
 * tick labels appear and how wide a share bar is can be tested directly. The
 * components in `charts.tsx` draw whatever these return.
 *
 * Everything here answers one of two questions:
 *
 *   - where does a value sit on a scale, and
 *   - which of these numbers does the reader actually see.
 *
 * The second is why `niceCeil` and `ticks` exist at all. A y-axis labelled with
 * the raw maximum puts its top gridline exactly on the tallest data point, so
 * the peak always touches the frame and there is no room to see how tall it is.
 * Rounding up to a clean number gives the headroom that makes a chart readable,
 * and it is the same reason the previous window is drawn as its own series
 * rather than as an annotation.
 */

export interface Pt { x: number; y: number }

/** Round a maximum up to a readable axis top.
 *
 *  1, 2, 2.5, 5 or 10 times a power of ten, and never below 1 — a scale whose
 *  top is 0.4 renders every value off the top of the chart.
 */
export function niceCeil(max: number): number {
  if (!isFinite(max) || max <= 0) return 1
  const exp = Math.floor(Math.log10(max))
  const pow = Math.pow(10, exp)
  const frac = max / pow
  const step = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 2.5 ? 2.5 : frac <= 5 ? 5 : 10
  return step * pow
}

/** Gridline values from 0 to `top`, inclusive.
 *
 *  Four intervals rather than "as many as fit", because the count should not
 *  change with the data — an axis that had three gridlines last week and five
 *  this week is a second thing to notice on a page whose job is noticing.
 */
export function ticks(top: number, count = 4): number[] {
  const out: number[] = []
  for (let i = 0; i <= count; i++) out.push((top / count) * i)
  return out
}

/** The polyline for a series, left to right.
 *
 *  `w`/`h` are the plot area, not the element: the caller reserves the axis
 *  gutter and passes what is left, so the math here never has to know about
 *  labels.
 *
 *  A single point has no line, so it is drawn as a dot by the caller. Returning
 *  an empty path rather than a lone coordinate pair keeps the caller from
 *  having to special-case it.
 */
export function linePath(values: number[], w: number, h: number, max: number): string {
  if (values.length < 2 || max <= 0) return ""
  const step = values.length > 1 ? w / (values.length - 1) : w
  return values
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(2)},${(h - (v / max) * h).toFixed(2)}`)
    .join(" ")
}

/** The same line, closed down to the baseline — for a filled area.
 *
 *  The baseline is the plot floor rather than zero-height inside the element,
 *  because the area has to meet the axis the line is measured from.
 */
export function areaPath(values: number[], w: number, h: number, max: number): string {
  const line = linePath(values, w, h, max)
  if (!line) return ""
  return `${line} L${w.toFixed(2)},${h} L0,${h} Z`
}

/** The x position of day `i` of `n`, matching `linePath`'s spacing.
 *
 *  Shared rather than recomputed in the component, because a hover position
 *  that is off by half a step from the line it is describing is worse than no
 *  hover at all — the tooltip would name a different day than the one under
 *  the cursor.
 */
export function xAt(i: number, n: number, w: number): number {
  if (n < 2) return w / 2
  return (i * w) / (n - 1)
}

/** The index of the day nearest a pointer position, for hover.
 *
 *  Clamped rather than wrapped: a pointer dragged past either end of the chart
 *  should report the first or last day, not wrap round to the other side.
 */
export function nearestIndex(px: number, n: number, w: number): number {
  if (n <= 1) return 0
  const step = w / (n - 1)
  return Math.max(0, Math.min(n - 1, Math.round(px / step)))
}

/** A share of a total as a percentage, for a bar or a label.
 *
 *  `null` for a zero total rather than "0%" — nothing happened is not the same
 *  as a zero-sized share of something, and a bar of width 0 next to a label
 *  reading 0% implies a denominator that was measured.
 */
export function share(n: number, total: number): number | null {
  if (!total) return null
  return (n * 100) / total
}

/** How much of a stacked bar a value occupies, 0..1, for an SVG width.
 *
 *  Separate from `share` because this one is a geometry and must be a number:
 *  a null would become NaN in a `width` attribute and the bar would vanish
 *  rather than read as empty.
 */
export function ratio(n: number, total: number): number {
  if (!total || n <= 0) return 0
  return n / total
}

/** Bar widths for a set of rows, in the order given.
 *
 *  Scaled to the largest row rather than to the total, because these are
 *  ranked rows and the point of the bar is to compare them with each other. A
 *  bar scaled to the total would be short on every row except the first and
 *  would say nothing about the shape of the list.
 */
export function barWidths(values: number[]): number[] {
  const max = Math.max(0, ...values)
  if (max <= 0) return values.map(() => 0)
  return values.map(v => ratio(v, max))
}

/** Moving average, for smoothing a series that is mostly zeroes.
 *
 *  `window` is the number of days either side, so the total span is
 *  2*window+1. A trailing average was the first version and it is wrong for
 *  this data: it leaves the first `window` days with no line at all, which on
 *  a 7-day range is most of the chart, and the point of the overlay is to be
 *  comparable along its whole length.
 *
 *  Edges use a shorter window rather than padding with zeroes, so the end of
 *  the series is not dragged down by days that were never in the range.
 */
export function smooth(values: number[], window = 3): number[] {
  if (values.length === 0) return []
  return values.map((_, i) => {
    const lo = Math.max(0, i - window)
    const hi = Math.min(values.length - 1, i + window)
    let sum = 0
    for (let j = lo; j <= hi; j++) sum += values[j]
    return sum / (hi - lo + 1)
  })
}

/** A compact label for an axis or a sparkline end.
 *
 *  1,200 rather than 1.2k, because these are read as counts and a rounded
 *  figure on an axis cannot be checked against the tile above it. Anything
 *  past 10,000 is where a compact form actually earns its place.
 */
export function compact(n: number): string {
  const a = Math.abs(n)
  if (a >= 1_000_000) return `${(n / 1_000_000).toFixed(a >= 10_000_000 ? 0 : 1)}M`
  if (a >= 10_000) return `${(n / 1000).toFixed(a >= 100_000 ? 0 : 1)}k`
  return Math.round(n).toLocaleString()
}
