import { describe, it, expect } from "vitest"
import {
  niceCeil, ticks, linePath, areaPath, xAt, nearestIndex,
  share, ratio, barWidths, smooth, compact,
} from "./chartMath"

describe("niceCeil", () => {
  it("rounds up so the peak is not touching the frame", () => {
    // The reason this function exists: with top = the raw maximum, the tallest
    // point sits exactly on the frame and its height cannot be read.
    expect(niceCeil(37)).toBeGreaterThan(37)
    expect(niceCeil(37)).toBe(50)
    // 4 is not on the 1/2/2.5/5 grid, so it rounds to 5 — which is the point.
    // What must hold is that the result is never below the data.
    expect(niceCeil(4)).toBe(5)
    expect(niceCeil(5)).toBe(5)          // already on the grid
    expect(niceCeil(4.2)).toBe(5)
  })

  it("never returns a scale that puts the data off the top", () => {
    for (const v of [0.4, 1, 3, 9, 11, 99, 101, 1234, 20_000]) {
      expect(niceCeil(v)).toBeGreaterThanOrEqual(v)
    }
  })

  it("survives the degenerate inputs a chart with no data produces", () => {
    expect(niceCeil(0)).toBe(1)
    expect(niceCeil(-5)).toBe(1)
    expect(niceCeil(NaN)).toBe(1)
    expect(niceCeil(Infinity)).toBe(1)
  })
})

describe("ticks", () => {
  it("always starts at zero and ends at the top", () => {
    const t = ticks(100)
    expect(t[0]).toBe(0)
    expect(t[t.length - 1]).toBe(100)
  })

  it("has the same number of lines whatever the data is", () => {
    // An axis that had three gridlines last week and five this week is a
    // second thing to notice on a page whose job is noticing things.
    expect(ticks(7).length).toBe(ticks(5000).length)
  })
})

describe("linePath", () => {
  it("puts the last point at the right edge and the first at the left", () => {
    const d = linePath([0, 50, 100], 300, 100, 100)
    expect(d).toBe("M0.00,100.00 L150.00,50.00 L300.00,0.00")
  })

  it("returns nothing for a single point, which has no line", () => {
    expect(linePath([5], 300, 100, 100)).toBe("")
    expect(linePath([], 300, 100, 100)).toBe("")
  })

  it("returns nothing rather than dividing by a zero max", () => {
    expect(linePath([0, 0, 0], 300, 100, 0)).toBe("")
  })

  it("scales every point against the max, not against the last one", () => {
    // The bug this guards: scaling to the final value would draw a rising
    // series that is actually flat. Value 50 against max 50 is the top of the
    // plot, so every point lands on y=0.
    const d = linePath([50, 50, 50], 200, 100, 50)
    expect(d.match(/,0\.00/g)?.length).toBe(3)
  })
})

describe("areaPath", () => {
  it("closes to the baseline so the fill meets the axis", () => {
    expect(areaPath([10, 20], 100, 50, 20)).toContain("L100.00,50 L0,50 Z")
  })

  it("is empty when there is no line to fill", () => {
    expect(areaPath([5], 100, 50, 20)).toBe("")
  })
})

describe("xAt / nearestIndex", () => {
  it("agrees with linePath's spacing, which is what makes hover trustworthy", () => {
    // A hover position half a step off the line it describes names a different
    // day than the one under the cursor, which is worse than no hover.
    const n = 31, w = 600
    const path = linePath(Array(n).fill(0), w, 100, 1)
    const lastX = Number(path.split("L").pop()!.split(",")[0])
    expect(lastX).toBeCloseTo(xAt(n - 1, n, w), 2)
  })

  it("clamps at both ends instead of wrapping round", () => {
    expect(nearestIndex(-500, 10, 100)).toBe(0)
    expect(nearestIndex(5000, 10, 100)).toBe(9)
  })

  it("finds the nearest day for a pointer position", () => {
    expect(nearestIndex(0, 3, 100)).toBe(0)
    expect(nearestIndex(49, 3, 100)).toBe(1)
    expect(nearestIndex(51, 3, 100)).toBe(1)
  })

  it("puts a single day in the middle rather than at the left edge", () => {
    expect(xAt(0, 1, 100)).toBe(50)
    expect(nearestIndex(0, 1, 100)).toBe(0)
  })
})

describe("share and ratio", () => {
  it("distinguishes nothing-happened from a zero-sized share", () => {
    // share() feeds labels, so it can say null. ratio() feeds an SVG width,
    // where null becomes NaN and the bar disappears instead of reading empty.
    expect(share(0, 100)).toBe(0)
    expect(share(5, 0)).toBeNull()
    expect(ratio(0, 0)).toBe(0)
    expect(ratio(5, 0)).toBe(0)
  })

  it("never returns NaN, whatever it is given", () => {
    for (const [a, b] of [[0, 0], [1, 0], [0, 1], [-5, 10], [5, -10]] as const) {
      expect(Number.isFinite(ratio(a, b))).toBe(true)
    }
  })
})

describe("barWidths", () => {
  it("scales to the largest row, because these rows are ranked", () => {
    // Scaled to the total, every bar but the first would be short and the
    // shape of the list would be invisible.
    expect(barWidths([100, 50, 0])).toEqual([1, 0.5, 0])
  })

  it("draws nothing for an all-zero list rather than dividing by zero", () => {
    expect(barWidths([0, 0, 0])).toEqual([0, 0, 0])
    expect(barWidths([])).toEqual([])
  })
})

describe("smooth", () => {
  it("does not leave the start of the series with no line at all", () => {
    // A trailing average leaves the first `window` days unplotted, which on a
    // 7-day range is most of the chart.
    const s = smooth([10, 20, 30, 40, 50], 1)
    expect(s).toHaveLength(5)
    expect(s.every(Number.isFinite)).toBe(true)
  })

  it("does not drag the ends down with days that were never in the range", () => {
    const flat = smooth([7, 7, 7, 7, 7], 2)
    expect(flat).toEqual([7, 7, 7, 7, 7])
  })

  it("keeps a spike visible but damped", () => {
    const s = smooth([0, 0, 100, 0, 0], 1)
    expect(s[2]).toBeGreaterThan(s[0])
    expect(s[2]).toBeLessThan(100)
  })

  it("survives an empty series", () => {
    expect(smooth([])).toEqual([])
  })
})

describe("compact", () => {
  it("leaves small counts exact, because they are checked against a tile", () => {
    expect(compact(0)).toBe("0")
    expect(compact(999)).toBe("999")
    expect(compact(1200)).toBe("1,200")
  })

  it("compacts only where a long number stops being readable", () => {
    expect(compact(12_345)).toBe("12.3k")
    expect(compact(250_000)).toBe("250k")
    expect(compact(1_500_000)).toBe("1.5M")
  })
})
