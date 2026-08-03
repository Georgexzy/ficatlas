import Link from "next/link"

// Next's built-in 404 is an unstyled 33-character page. That was invisible when
// the only visitors were on the tailnet; on a public site it is the page every
// dead link, stale bookmark and scanner lands on, and it should look like the
// rest of the app and offer the way back.
export const metadata = { title: "Not found — FicAtlas" }

export default function NotFound() {
  return (
    <div className="empty">
      <h1 className="empty__title">Nothing here</h1>
      <p className="empty__sub">
        That page doesn&apos;t exist. The story may have been removed from the
        index, or the link may be mistyped.
      </p>
      <Link href="/" className="card-btn card-btn--primary">Search FicAtlas</Link>
    </div>
  )
}
