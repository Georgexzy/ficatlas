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
      <Link href="/takedown">Remove my story</Link>
      <span className="site-footer__sep">·</span>
      <a href="/robots.txt">Crawler policy</a>
      <span className="site-footer__sep">·</span>
      <a href="https://github.com/Georgexzy/ficatlas" target="_blank" rel="noopener noreferrer">Source</a>
    </footer>
  )
}
