"use client"

/**
 * Charts, drawn as SVG.
 *
 * No charting library. The project already has exactly one runtime dependency
 * beyond React, and these are four shapes — a line, a share bar, a funnel, a
 * sparkline — so a library would be several hundred kilobytes to redraw what
 * is about two hundred lines here, and every chart would then be a black box
 * that cannot be styled to match the rest of the page.
 *
 * The geometry lives in `chartMath.ts`, which has no React in it and is tested
 * directly. What is here is the drawing and the accessibility: every chart
 * that carries numbers also carries those numbers as text, because a canvas
 * nobody can read is a picture of a chart rather than a chart.
 */

import { useId, useState } from "react"
import {
  niceCeil, ticks, linePath, areaPath, xAt, nearestIndex, barWidths, ratio, compact,
} from "./chartMath"

// ── a trend over time, with the previous window drawn against it ─────────────

export interface DayPoint {
  day: string
  views: number
  searches: number
  visitors: number
}

export interface Series { key: string; label: string; color: string }

const VIEWS: Series = { key: "views", label: "Pageviews", color: "var(--chart-1)" }
const SEARCHES: Series = { key: "searches", label: "Searches", color: "var(--chart-2)" }

/** Format a day for a tooltip, without the year when it is the current one. */
function dayLabel(iso: string, withYear = false): string {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    ...(withYear ? { year: "numeric" as const } : {}),
  })
}

/**
 * The daily series, with the SAME window immediately before it underneath.
 *
 * The comparison is the reason this is a chart and not a table of numbers. A
 * tile can say "up 42%" and the reader has nothing to compare the shape
 * against — whether this week was steady, or one big day followed by silence,
 * is invisible in a percentage. Drawing the previous window at the same width
 * on the same scale answers it at a glance, and it is also the honest way to
 * show a decline: a 42% drop is a line below another line.
 *
 * The two windows are aligned by POSITION, not by date, so day 1 of this
 * window sits above day 1 of the one before it. That is what makes the shapes
 * comparable; aligning them by calendar date would leave a gap wherever the
 * two windows do not overlap in weekday, which is most of them.
 */
export function TrendChart({ days, previous, height = 190 }: {
  days: DayPoint[]
  /** Totals for the window before, if the caller has them. */
  previous?: { days: DayPoint[] } | null
  height?: number
}) {
  const [hover, setHover] = useState<number | null>(null)
  const gradViews = useId(), gradSearch = useId()

  if (days.length === 0) return null

  // One scale for every series on the chart, including the overlay. Scaling
  // them separately would make 3 searches as tall as 40 pageviews and quietly
  // turn the chart into two unrelated pictures.
  const peak = Math.max(
    1,
    ...days.map(d => Math.max(d.views, d.searches)),
    ...(previous?.days ?? []).map(d => Math.max(d.views, d.searches)),
  )
  const top = niceCeil(peak)
  const grid = ticks(top)

  // The plot area. The left gutter holds the y labels and the bottom strip
  // holds the x labels, and both are reserved here rather than inside the
  // chart so `chartMath`'s coordinates are always plot-area coordinates.
  const PAD_L = 42, PAD_R = 8, PAD_T = 10, PAD_B = 26
  const VB_W = 720
  const w = VB_W - PAD_L - PAD_R
  const h = height - PAD_T - PAD_B

  const px = (i: number) => PAD_L + xAt(i, days.length, w)
  const py = (v: number) => PAD_T + h - (v / top) * h

  const views = days.map(d => d.views)
  const searches = days.map(d => d.searches)
  const prevViews = previous?.days?.map(d => d.views) ?? null
  const prevSearches = previous?.days?.map(d => d.searches) ?? null

  const shown = hover != null ? days[Math.min(hover, days.length - 1)] : null
  // Roughly eight x labels whatever the range, always including the last.
  const step = Math.max(1, Math.ceil(days.length / 8))

  return (
    <div className="chart">
      <svg
        viewBox={`0 0 ${VB_W} ${height}`}
        className="chart__svg"
        role="img"
        aria-label={
          `Pageviews and searches per day over ${days.length} days` +
          (previous?.days?.length ? ", with the previous window behind" : "")
        }
        onMouseLeave={() => setHover(null)}
        onMouseMove={e => {
          const box = e.currentTarget.getBoundingClientRect()
          // The viewBox is scaled to the rendered width, so the pointer has to
          // be mapped back through it. Reading offsetX directly would be in
          // CSS pixels and would drift with the container.
          const rel = ((e.clientX - box.left) / box.width) * VB_W
          setHover(nearestIndex(rel - PAD_L, days.length, w))
        }}
      >
        <defs>
          <linearGradient id={gradViews} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-1)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--chart-1)" stopOpacity="0.02" />
          </linearGradient>
          <linearGradient id={gradSearch} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-2)" stopOpacity="0.24" />
            <stop offset="100%" stopColor="var(--chart-2)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Gridlines and the y axis. Behind everything, so a line crossing one
            is not interrupted by it. */}
        {grid.map(v => (
          <g key={v}>
            <line x1={PAD_L} x2={PAD_L + w} y1={py(v)} y2={py(v)}
              className="chart__grid" />
            <text x={PAD_L - 8} y={py(v) + 4} className="chart__ylabel">
              {compact(v)}
            </text>
          </g>
        ))}

        {/* The previous window first, so it sits UNDER this one. Dashed and
            unlabelled on the line itself: it is context, and a reader who has
            to work out which of two solid lines is real has been asked to do
            the chart's job. */}
        {prevViews && prevViews.length > 1 && (
          <>
            <path d={shift(linePath(prevViews, w, h, top), PAD_L, PAD_T)}
              className="chart__prev" style={{ stroke: VIEWS.color }} />
            <path d={shift(linePath(prevSearches!, w, h, top), PAD_L, PAD_T)}
              className="chart__prev" style={{ stroke: SEARCHES.color }} />
          </>
        )}

        <path d={shift(areaPath(views, w, h, top), PAD_L, PAD_T)} fill={`url(#${gradViews})`} />
        <path d={shift(areaPath(searches, w, h, top), PAD_L, PAD_T)} fill={`url(#${gradSearch})`} />
        <path d={shift(linePath(views, w, h, top), PAD_L, PAD_T)}
          className="chart__line" style={{ stroke: VIEWS.color }} />
        <path d={shift(linePath(searches, w, h, top), PAD_L, PAD_T)}
          className="chart__line" style={{ stroke: SEARCHES.color }} />

        {/* The hovered day. A full-height rule rather than a dot, because the
            question on hover is "which day", and the day is identified by its
            x position across the whole chart. */}
        {hover != null && days[hover] && (
          <>
            <line x1={px(hover)} x2={px(hover)} y1={PAD_T} y2={PAD_T + h}
              className="chart__cursor" />
            <circle cx={px(hover)} cy={py(days[hover].views)} r={3.5}
              fill="var(--chart-1)" />
            <circle cx={px(hover)} cy={py(days[hover].searches)} r={3.5}
              fill="var(--chart-2)" />
          </>
        )}

        {/* X labels. Every day is IN the chart — the gaps are the story on a
            site this quiet — but only some of them can be named. */}
        {days.map((d, i) => (i === days.length - 1 || (days.length - 1 - i) % step === 0) && (
          <text key={d.day} x={px(i)} y={height - 8} className="chart__xlabel"
            textAnchor={i === days.length - 1 ? "end"
              : i === 0 ? "start" : "middle"}>
            {dayLabel(d.day).replace(/^[A-Za-z]+,?\s/, "")}
          </text>
        ))}
      </svg>

      {/* The numbers, as text. This is the accessible half and the half that
          survives a screenshot: everything the chart encodes as height is also
          written down here. */}
      {shown && (
        <div className="chart__readout" role="status">
          <strong>{dayLabel(shown.day, true)}</strong>
          <span><i style={{ background: VIEWS.color }} />
            {shown.views.toLocaleString()} pageviews</span>
          <span><i style={{ background: SEARCHES.color }} />
            {shown.searches.toLocaleString()} searches</span>
          <span className="chart__readout-dim">
            {shown.visitors.toLocaleString()} people
          </span>
        </div>
      )}
    </div>
  )
}

/** Offset a path built in plot coordinates into the chart's own coordinates.
 *
 *  Done as a string rewrite rather than by passing the padding into
 *  `linePath`, because the padding is a layout concern and the maths should
 *  not have to know the chart has labels.
 */
function shift(d: string, dx: number, dy: number): string {
  if (!d) return d
  return d.replace(/(-?\d+\.?\d*),(-?\d+\.?\d*)/g,
    (_, x, y) => `${(Number(x) + dx).toFixed(2)},${(Number(y) + dy).toFixed(2)}`)
}

// ── a bar for one row of a table ─────────────────────────────────────────────

/**
 * A share bar behind a number, for a ranked table.
 *
 * The number is the data and the bar is the comparison, which is why the bar
 * is behind the text and not a separate column: a reader scanning for "how
 * much" reads the digits, and one scanning for "shape" reads the bars, and
 * neither has to reconcile the two.
 *
 * Scaled to the largest row rather than to the total, because these rows are
 * already ranked and the question is how they compare with each other.
 */
export function ShareBar({ value, max, className = "" }:
  { value: number; max: number; className?: string }) {
  const w = ratio(value, max) * 100
  return (
    <span className={"sharebar " + className} aria-hidden="true">
      <span className="sharebar__fill" style={{ width: `${w}%` }} />
    </span>
  )
}

// ── the funnel, drawn as a funnel ────────────────────────────────────────────

export interface FunnelStep {
  label: string
  value: number
  /** What the same step was in the previous window, when there is one. */
  before?: number
  /** A caveat that must sit next to the number rather than under the chart. */
  note?: string
}

/**
 * The funnel as a narrowing bar per step.
 *
 * Widths are proportional to the LARGEST step, not to each step's own
 * predecessor, so a bar is readable as "this share of everybody who got
 * here" rather than as an angle whose meaning depends on the step above it.
 * Consecutive proportional widths are a set of ratios the reader has to
 * multiply up in their head, and the number printed on each step is the
 * conversion they should not have to do.
 *
 * The step-to-step rate is the thing worth seeing and it is not derivable from
 * the widths above, so it is printed between the bars rather than left to be
 * inferred.
 */
export function Funnel({ steps, total }:
  { steps: FunnelStep[]; total: number }) {
  const max = Math.max(1, ...steps.map(s => s.value))
  return (
    <ol className="funnel">
      {steps.map((s, i) => {
        const prev = i > 0 ? steps[i - 1] : null
        const ofPrev = prev && prev.value > 0
          ? Math.round((s.value / prev.value) * 100)
          : null
        return (
          <li key={s.label} className="funnel__step">
            <div className="funnel__head">
              <span className="funnel__label">{s.label}</span>
              <span className="funnel__value">{s.value.toLocaleString()}</span>
            </div>
            <ShareBar value={s.value} max={max} className="funnel__bar" />
            <div className="funnel__foot">
              {ofPrev !== null && (
                <span className="funnel__rate">
                  {ofPrev}% of the step above
                </span>
              )}
              {total > 0 && (
                <span className="funnel__share">
                  {Math.round((s.value / total) * 100)}% of all readers
                </span>
              )}
              {s.before !== undefined && s.before > 0 && s.before + s.value >= 20 && (
                <span className={"funnel__delta " +
                  (s.value >= s.before ? "funnel__delta--up" : "funnel__delta--down")}>
                  {s.value >= s.before ? "▲" : "▼"}{" "}
                  {Math.abs(Math.round(((s.value - s.before) / s.before) * 100))}%
                </span>
              )}
              {s.note && <span className="funnel__note">{s.note}</span>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

// ── a sparkline, for a tile ──────────────────────────────────────────────────

/**
 * A tile's own trend, at the size a tile can carry.
 *
 * A sparkline is a SHAPE, not a chart: no axes, no labels, no interaction. It
 * exists so a number that went up or down can be seen rather than computed, and
 * the direction is the whole message. The dot on the last point is what makes
 * "where are we now" readable at that size — without it the line has no
 * discernible end.
 */
export function Sparkline({ values, color = "var(--chart-1)", width = 108, height = 30 }:
  { values: number[]; color?: string; width?: number; height?: number }) {
  if (values.length < 2) return null
  const top = niceCeil(Math.max(1, ...values))
  const d = linePath(values, width, height - 4, top)
  const lastX = xAt(values.length - 1, values.length, width)
  const lastY = (height - 4) - (values[values.length - 1] / top) * (height - 4)
  return (
    <svg width={width} height={height} className="spark" aria-hidden="true">
      <path d={d} fill="none" stroke={color} strokeWidth="1.75"
        strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lastX} cy={lastY} r="2.5" fill={color} />
    </svg>
  )
}

// ── a ranked horizontal bar chart, for a list rather than a series ───────────

export interface BarRow { label: string; value: number; hint?: string }

/**
 * A ranked list as bars.
 *
 * For the "where did they come from" and "how did the edge answer" tables,
 * where the ORDER is the information and there is no time axis. Bars rather
 * than a pie: the comparison people make is "how much bigger is the first than
 * the second", and a pie makes that a judgement about angles while a bar makes
 * it a subtraction.
 */
export function RankedBars({ rows, total, color = "var(--chart-1)", fmt }: {
  rows: BarRow[]
  total?: number
  color?: string
  fmt?: (n: number) => string
}) {
  const widths = barWidths(rows.map(r => r.value))
  const sum = total ?? rows.reduce((a, b) => a + b.value, 0)
  if (rows.length === 0) return null
  return (
    <ul className="ranked">
      {rows.map((r, i) => (
        <li key={r.label + i} className="ranked__row">
          <span className="ranked__label" title={r.label}>{r.label}</span>
          <span className="ranked__track">
            <span className="ranked__fill"
              style={{ width: `${widths[i] * 100}%`, background: color }} />
          </span>
          <span className="ranked__value">{(fmt ?? ((n: number) => n.toLocaleString()))(r.value)}</span>
          {sum > 0 && (
            <span className="ranked__share">
              {r.value / sum < 0.001 && r.value > 0 ? "<0.1" : (r.value / sum * 100).toFixed(1)}%
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

export { VIEWS as VIEWS_SERIES, SEARCHES as SEARCHES_SERIES }
