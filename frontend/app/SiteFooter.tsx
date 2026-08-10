import Link from "next/link"

// Rendered on every page from the root layout. Deliberately small: it is not
// selling anything, it exists so that a stranger can tell what this site is and
// an author can find the takedown route without being told where to look.
export default function SiteFooter() {
  return (
    <footer className="site-footer">
      <span>FicAtlas — search AO3, FanFiction.net and FicAlley together</span>
      <span className="site-footer__sep">·</span>
      <Link href="/about">About</Link>
      <span className="site-footer__sep">·</span>
      <Link href="/about#ai">AI policy</Link>
      <span className="site-footer__sep">·</span>
      <Link href="/takedown">Remove my story</Link>
      <span className="site-footer__sep">·</span>
      {/* Same reasoning as the takedown link beside it: an author should find
          this without being told where to look. It was reachable only from the
          foot of a story page, which assumes they already found their own work
          here — and someone who has just heard the site exists has not.
          Points at /permissions rather than /permissions/manage because that
          page explains all three options, including the two that need nothing
          from them, and links onward to the list. */}
      <Link href="/permissions">I&apos;m an author</Link>
      <span className="site-footer__sep">·</span>
      <a href="/robots.txt">Crawler policy</a>
      <span className="site-footer__sep">·</span>
      <a href="https://github.com/Georgexzy/ficatlas" target="_blank" rel="noopener noreferrer">Source</a>
    </footer>
  )
}
