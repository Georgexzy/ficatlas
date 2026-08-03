"use client"
import { useCallback, useEffect, useRef, useState } from "react"

// A two-handle range slider for word count.
//
// The presets and the two number boxes stay — they are better for "exactly
// 100k+" and for typing a figure. This is for the other way people choose a
// length: sliding until the range feels right, which neither of those does.
//
// The scale is LOGARITHMIC. Word counts in this index run from 100 to about
// 1.5M, and on a linear scale everything under 50k — which is most of the
// index — is crushed into the first 3% of the track, so the handle cannot
// separate a 5k one-shot from a 40k novella. A log scale gives each order of
// magnitude the same room.
const MIN_WORDS = 100
const MAX_WORDS = 1_000_000

const toPct = (words: number) => {
  const lo = Math.log10(MIN_WORDS), hi = Math.log10(MAX_WORDS)
  const clamped = Math.min(MAX_WORDS, Math.max(MIN_WORDS, words))
  return ((Math.log10(clamped) - lo) / (hi - lo)) * 100
}

const fromPct = (pct: number) => {
  const lo = Math.log10(MIN_WORDS), hi = Math.log10(MAX_WORDS)
  const raw = 10 ** (lo + (pct / 100) * (hi - lo))
  // Round to something a person would type, so the readout is not "37,412".
  const step = raw < 1000 ? 100 : raw < 10_000 ? 500 : raw < 100_000 ? 1000 : 10_000
  return Math.round(raw / step) * step
}

const fmt = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M`
  : n >= 1000 ? `${Math.round(n / 1000)}k`
  : String(n)

export default function WordCountSlider(
  { min, max, onChange }:
  { min?: number; max?: number; onChange: (min?: number, max?: number) => void },
) {
  const trackRef = useRef<HTMLDivElement | null>(null)
  const [drag, setDrag] = useState<"min" | "max" | null>(null)

  const lo = min ?? MIN_WORDS
  const hi = max ?? MAX_WORDS
  const loPct = toPct(lo)
  const hiPct = toPct(hi)

  const valueAt = useCallback((clientX: number) => {
    const el = trackRef.current
    if (!el) return null
    const r = el.getBoundingClientRect()
    const pct = ((clientX - r.left) / r.width) * 100
    return fromPct(Math.min(100, Math.max(0, pct)))
  }, [])

  const move = useCallback((clientX: number, which: "min" | "max") => {
    const v = valueAt(clientX)
    if (v == null) return
    if (which === "min") {
      // An unset bound means "no limit", so only store a value once it has
      // actually been moved off the end of the track.
      onChange(v <= MIN_WORDS ? undefined : Math.min(v, hi), max)
    } else {
      onChange(min, v >= MAX_WORDS ? undefined : Math.max(v, lo))
    }
  }, [valueAt, onChange, min, max, lo, hi])

  useEffect(() => {
    if (!drag) return
    const onMove = (e: PointerEvent) => move(e.clientX, drag)
    const onUp = () => setDrag(null)
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
    window.addEventListener("pointercancel", onUp)
    return () => {
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
      window.removeEventListener("pointercancel", onUp)
    }
  }, [drag, move])

  // Arrow keys nudge by one step of the log scale, so the slider is usable
  // without a pointer at all.
  const onKey = (e: React.KeyboardEvent, which: "min" | "max") => {
    const cur = which === "min" ? lo : hi
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0
    if (!dir) return
    e.preventDefault()
    const next = fromPct(Math.min(100, Math.max(0, toPct(cur) + dir * 2)))
    if (which === "min") onChange(next <= MIN_WORDS ? undefined : Math.min(next, hi), max)
    else onChange(min, next >= MAX_WORDS ? undefined : Math.max(next, lo))
  }

  return (
    <div className="wc-slider">
      <div className="wc-slider__readout">
        <span>{min == null ? "Any" : fmt(lo)}</span>
        <span className="wc-slider__sep">–</span>
        <span>{max == null ? "Any" : fmt(hi)}</span>
        {(min != null || max != null) && (
          <button className="wc-slider__clear" onClick={() => onChange(undefined, undefined)}>
            Clear
          </button>
        )}
      </div>

      <div className="wc-slider__track" ref={trackRef}
        // Clicking the track moves whichever handle is nearer, which is what
        // people expect and saves a drag for coarse adjustments.
        onPointerDown={(e) => {
          const v = valueAt(e.clientX)
          if (v == null) return
          const which = Math.abs(toPct(v) - loPct) < Math.abs(toPct(v) - hiPct) ? "min" : "max"
          setDrag(which)
          move(e.clientX, which)
        }}>
        <div className="wc-slider__fill"
          style={{ left: `${loPct}%`, width: `${Math.max(0, hiPct - loPct)}%` }} />
        <div
          className={`wc-slider__handle ${drag === "min" ? "is-dragging" : ""}`}
          style={{ left: `${loPct}%` }}
          role="slider" tabIndex={0}
          aria-label="Minimum word count"
          aria-valuemin={MIN_WORDS} aria-valuemax={MAX_WORDS} aria-valuenow={lo}
          aria-valuetext={min == null ? "no minimum" : `${lo.toLocaleString()} words`}
          onPointerDown={(e) => { e.stopPropagation(); setDrag("min") }}
          onKeyDown={(e) => onKey(e, "min")}
        />
        <div
          className={`wc-slider__handle ${drag === "max" ? "is-dragging" : ""}`}
          style={{ left: `${hiPct}%` }}
          role="slider" tabIndex={0}
          aria-label="Maximum word count"
          aria-valuemin={MIN_WORDS} aria-valuemax={MAX_WORDS} aria-valuenow={hi}
          aria-valuetext={max == null ? "no maximum" : `${hi.toLocaleString()} words`}
          onPointerDown={(e) => { e.stopPropagation(); setDrag("max") }}
          onKeyDown={(e) => onKey(e, "max")}
        />
      </div>

      <div className="wc-slider__scale" aria-hidden="true">
        <span>100</span><span>1k</span><span>10k</span><span>100k</span><span>1M</span>
      </div>
    </div>
  )
}
