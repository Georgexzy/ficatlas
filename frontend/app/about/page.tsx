import Link from "next/link"
import BackLink from "../BackLink"
import SiteHeader from "../SiteHeader"

export const metadata = {
  title: "About & contact",   // layout.tsx appends " · FicAtlas"
  description:
    "What FicAtlas is, how it treats fanworks and AI, where its data comes from, and how to ask for a story to be taken down.",
  // The root layout deliberately sets no canonical (see the note there), so a
  // page without one has none at all — which is what Search Console means by
  // "duplicate without user-selected canonical".
  alternates: { canonical: "/about" },
}

// A public site needs a page that says what it is and how to reach a human.
// This one carries the takedown route as well, because an author who wants
// their work removed should not have to hunt for it — that is the single most
// important thing on this page for the person most likely to need it.
export default function About() {
  return (
    <main id="main" className="page-prose">
      {/* Was a lone "← Back to search" pointing at "/", which threw away
          whatever you had searched. The shared header instead: same one click
          home, plus Library and Settings, and its Search remembers your
          results. */}
      <SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />

      <h1>About FicAtlas</h1>
      <p>
        FicAtlas is a search engine for fanfiction. It indexes work from{" "}
        <strong>Archive of Our Own</strong>, <strong>FanFiction.net</strong> and{" "}
        <strong>FictionAlley</strong> so you can search all three at once, rather than
        searching each one and missing the other two.
      </p>
      <p>
        It is an independent, non-commercial project. There is no advertising,
        no third-party analytics, and no data is sold. FicAtlas does keep
        short-lived, first-party traffic counts; the <Link href="/privacy">privacy page</Link>
        explains what they contain and when they are deleted.
      </p>

      <h2>Where the writing lives</h2>
      <p>
        Most indexed works have a listing — title, author, summary, tags,
        length and a link to the original archive. Readers follow that link to
        read the work where it was published.
      </p>
      <p>
        Some works can also be read here in full. These include works preserved
        from the closed FictionAlley archive and works imported for reading on
        FicAtlas. A reader&apos;s private import is visible only to accounts
        that independently imported the same work. Authors can request removal
        or restrict future hosting through the author page below.
      </p>

      {/* THE AUTHOR POLICY IS NOT WRITTEN HERE ANY MORE.
          It was, at length — a takedown section and a terms section, roughly
          forty lines — and the same four facts were also on /permissions and
          again on /takedown. Three statements of one policy in three voices,
          which is how /permissions came to be quoting "~19.9 million" against
          an index of 20.8M: nothing keeps three copies honest.

          /permissions is the single author page now. This one keeps the route
          to it, because an author who has found their work somewhere they did
          not put it must be able to reach the door from here without reading a
          policy first, and that is the most important link on this page for the
          person most likely to need it. */}
      <h2 id="takedown">For authors</h2>
      <p>
        If your work appears here and you would rather it did not, you can
        request that its text or listing be withdrawn. No account or proof of
        identity is required. A matching request normally takes effect on
        submission; requests that cannot be matched or exceed the automatic
        safety limit are recorded for review.
      </p>
      <p>
        You can also review everything held under your name, remove individual
        works, or set a standing preference that applies to your whole catalogue
        and to anything you publish later.
      </p>
      <p>
        <Link href="/permissions" className="card-btn card-btn--primary">
          Review and manage my work
        </Link>
      </p>
      <p className="page-prose__muted">
        Granting permission to host a complete copy requires verification of
        your archive profile. The full policy and request forms are on that page.
      </p>

      {/* The page's own title has said "About & contact" since it was written,
          and until now the contact route was a sentence inside the takedown
          section — which went when that section was consolidated onto
          /permissions, leaving a page that promised contact details and gave
          none. Its own section now, so it cannot be lost to an edit elsewhere.

          The form is named first deliberately: it writes to the same queue an
          operator works, so it does not depend on an inbox being watched. */}
      <h2 id="contact">Contact</h2>
      <p>
        For anything concerning a specific work — removal, or setting your terms
        as its author — the{" "}
        <Link href="/permissions">author page</Link> is the fastest route, and
        it records the request directly rather than relying on mail being read.
      </p>
      <p>
        For anything else, including questions about this site or a problem with
        it, write to{" "}
        <a href="mailto:help@ficatlas.com">help@ficatlas.com</a>.
      </p>

      <h2>Source code</h2>
      <p>
        FicAtlas publishes its source code. You can read it or contribute a fix:{" "}
        <a href="https://github.com/Georgexzy/ficatlas" target="_blank" rel="noopener noreferrer">
          github.com/Georgexzy/ficatlas
        </a>.
      </p>
      <p className="page-prose__muted">
        The software is licensed under the{" "}
        <a href="https://github.com/Georgexzy/ficatlas/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">
          PolyForm Noncommercial License 1.0.0
        </a>. That licence covers the software, not the works it indexes.
      </p>

      <h2 id="ai">AI and automated collection</h2>
      <p>
        FicAtlas does not use indexed works to train generative models. Its
        current approach to automated collection and AI is:
      </p>
      <ul>
        <li>
          <strong>Search does not generate story text.</strong> Search and
          recommendations use indexed information; FicAtlas does not offer a
          chat interface for reproducing or continuing authors&apos; prose.
        </li>
        <li>
          <strong>The index is not offered for model training.</strong> FicAtlas
          does not publish a bulk text export or license works for machine
          learning. Known training crawlers are disallowed in{" "}
          <a href="/robots.txt">robots.txt</a>.
        </li>
        <li>
          <strong>Most works are listings.</strong> Full text is also held for
          preserved works and imports, as described above. Authors can request
          withdrawal of text or listings through the author page.
        </li>
        <li>
          <strong>Software development.</strong> AI programming assistance has
          been used to write parts of FicAtlas&apos;s code and search tooling.
        </li>
        <li>
          <strong>Collection is rate-limited and bounded.</strong> Metadata is
          gathered slowly, with backoff, and is taken from the Internet Archive
          wherever that avoids load on a live archive.
        </li>
      </ul>
      <p>
        To request removal of your work, use the{" "}
        <Link href="/takedown">removal form</Link>.
      </p>
    </main>
  )
}
