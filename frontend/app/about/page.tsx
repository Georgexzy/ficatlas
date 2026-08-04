import Link from "next/link"

export const metadata = {
  title: "About & contact — FicAtlas",
  description: "What FicAtlas is, where its data comes from, and how to ask for a story to be taken down.",
}

// A public site needs a page that says what it is and how to reach a human.
// This one carries the takedown route as well, because an author who wants
// their work removed should not have to hunt for it — that is the single most
// important thing on this page for the person most likely to need it.
export default function About() {
  return (
    <div className="page-prose">
      <p className="page-prose__back"><Link href="/">← Back to search</Link></p>

      <h1>About FicAtlas</h1>
      <p>
        FicAtlas is a search engine for fanfiction. It indexes work from{" "}
        <strong>Archive of Our Own</strong>, <strong>FanFiction.net</strong> and{" "}
        <strong>FicAlley</strong> so you can search all three at once, rather than
        searching each one and missing the other two.
      </p>
      <p>
        It is a personal, non-commercial project. There are no adverts, no
        trackers, and nothing is sold.
      </p>

      <h2>Where the writing lives</h2>
      <p>
        For nearly everything in the index, FicAtlas stores{" "}
        <strong>only information about a story</strong> — its title, author,
        summary, tags, length and a link — and sends you to the original archive
        to read it. Authors keep their work, their comments and their kudos where
        they posted them.
      </p>
      <p>
        A small number of stories can be read here directly. These come from{" "}
        <strong>FicAlley</strong>, an archive that shut down, and were preserved
        so they would not disappear. If you wrote one of them, the section below
        is for you.
      </p>

      <h2 id="takedown">Asking for a story to be removed</h2>
      <p>
        If you are the author of a story whose text can be read on FicAtlas and
        you would rather it were not, you can have it removed. You do not need to
        explain yourself, and you do not need to prove anything first.
      </p>
      <p>
        <strong>The text comes down straight away</strong>, as soon as the form is
        submitted — not after a review. The story stays listed as a title, author
        and link, so people can still find your work where you publish it now.
      </p>
      <p>
        <Link href="/takedown" className="card-btn card-btn--primary">
          Request a takedown
        </Link>
      </p>
      <p className="page-prose__muted">
        If you would rather write to a person, or you want the listing removed as
        well, email <strong>admin@ficatlas.app</strong> and say so.
      </p>

      <h2>Source code</h2>
      <p>
        FicAtlas is open source. You can read every line, run your own copy, or
        send a fix:{" "}
        <a href="https://github.com/Georgexzy/ficatlas" target="_blank" rel="noopener noreferrer">
          github.com/Georgexzy/ficatlas
        </a>.
      </p>
      <p className="page-prose__muted">
        Licensed for non-commercial use. You are welcome to run it for yourself
        or your corner of fandom; you may not sell it or run it with adverts.
        That restriction exists for the authors whose work it indexes, not for
        me — they published for free, on archives that promised not to profit
        from them.
      </p>

      <h2>Crawling and AI</h2>
      <p>
        FicAtlas gathers metadata slowly and politely, and reads archived copies
        from the Internet Archive wherever it can, to keep load off the archives
        themselves. It does not provide bulk data to AI training crawlers, which
        are refused in{" "}
        <a href="/robots.txt">robots.txt</a>.
      </p>
    </div>
  )
}
