"use client"

// DarkLordPotter's community star rating, drawn the way DLP itself draws it:
// five stars with the numeric value beside them.
//
// The value arrives as a `dlp_stars:4.67` tag rather than a column, because it
// is only meaningful for the few hundred works on DLP's curated list and does
// not warrant a column across 19.6M rows. Rendering it from the tag also means
// the tag can stay out of the visible tag list, where "dlp_stars:4.67" was
// showing up as a chip among the content tags.
export function dlpRating(tags?: string[] | null): number | null {
  const tag = (tags ?? []).find(t => t.startsWith("dlp_stars:"))
  if (!tag) return null
  const value = Number(tag.split(":")[1])
  return Number.isFinite(value) && value > 0 ? value : null
}

export default function DlpStars({ value }: { value: number }) {
  // Halves matter here: DLP's own display distinguishes 3.5 from 4, and the
  // ratings cluster tightly between 3.5 and 5, so rounding to whole stars would
  // make almost every work look identical.
  const stars = [1, 2, 3, 4, 5].map(i => {
    if (value >= i - 0.25) return "full"
    if (value >= i - 0.75) return "half"
    return "empty"
  })

  return (
    <span className="dlp-stars"
      title={`DarkLordPotter community rating: ${value.toFixed(2)} out of 5`}
      aria-label={`Dark Lord Potter rating ${value.toFixed(2)} out of 5`}>
      <span className="dlp-stars__row" aria-hidden="true">
        {stars.map((kind, i) => (
          <span key={i} className={`dlp-stars__star dlp-stars__star--${kind}`}>
            {kind === "half" ? "★" : "★"}
          </span>
        ))}
      </span>
      <span className="dlp-stars__value">{value.toFixed(2)}</span>
    </span>
  )
}
