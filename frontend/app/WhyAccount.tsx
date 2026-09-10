import Link from "next/link"

/**
 * What an account on FicAtlas actually does.
 *
 * There were two accounts on this site. The reasons to make one existed and
 * worked — following WIPs for updates, a shelf that survives a wiped browser,
 * reading progress that follows you from a laptop to a phone — and were stated
 * in exactly two places, both of them behind the decision: a line in the signed
 * in user menu, and a note on the library page. Someone deciding whether to
 * bother had a username field, a password field, and a sentence.
 *
 * Every item here is a thing the code really does; nothing is aspirational.
 * Note what following is NOT: there is no notification queue and nothing is
 * emailed. An update is a comparison made at read time — see the header of
 * backend/api/follows.py — so the copy says "shows which have gained chapters
 * since you last looked", which is what it does.
 * `follows` is backend/api/follows.py, the sync buckets are DATA_GROUPS in
 * lib/localdata.ts, and the email field is optional at signup and only ever
 * used for a reset (backend/api/password_reset.py). If a feature is removed,
 * remove the line — a list of promises that has drifted is worse than no list,
 * because this is the screen where a reader decides whether the site is honest.
 *
 * Ordered by what a fanfiction reader actually loses without it, not by what is
 * most impressive to build. Update alerts first: every reader of an unfinished
 * work has the problem of not knowing when it continues, and no archive solves
 * it across archives.
 */

const REASONS: { title: string; body: string }[] = [
  {
    title: "Know when a WIP updates",
    body: "Follow unfinished stories and your Following list shows which have "
        + "gained chapters since you last looked, with a count in the menu — one "
        + "list covering AO3, FanFiction.net and FictionAlley, which no single "
        + "archive's subscriptions can do.",
  },
  {
    title: "Nothing lands in your inbox",
    body: "Updates are shown when you come back, not emailed at you. An address "
        + "is optional and is only ever used to reset a password.",
  },
  {
    title: "A shelf that outlives your browser",
    body: "Bookmarks are kept in this browser until you have an account. Clear "
        + "your history, switch to your phone, or use a different browser and "
        + "they are gone.",
  },
  {
    title: "Pick up where you stopped",
    body: "Reading progress and your place in a chapter follow you between "
        + "devices, so the fic you started on a laptop opens at the right "
        + "paragraph on a phone.",
  },
  {
    title: "Your searches and filters, kept",
    body: "Recent searches, the archives you prefer, and your never-show-me "
        + "list travel with the account instead of living in one browser.",
  },
  {
    title: "Reader settings that stay put",
    body: "Theme, font, text size and line width apply everywhere you sign in.",
  },
]

export default function WhyAccount({ compact = false }: { compact?: boolean }) {
  return (
    <section className="why-account" aria-labelledby="why-account-h">
      <h2 className="why-account__h" id="why-account-h">
        {compact ? "What an account gets you" : "Why make an account"}
      </h2>
      <ul className="why-account__list">
        {REASONS.map(r => (
          <li key={r.title} className="why-account__item">
            <span className="why-account__title">{r.title}</span>
            <span className="why-account__body">{r.body}</span>
          </li>
        ))}
      </ul>
      {/* The catch, said before anyone asks. This site's whole pitch is that it
          is not doing anything with you, and an account is the one place a
          reader is right to be suspicious — so the limits are stated here
          rather than in a policy nobody opens. Every clause is enforced
          somewhere real: signup takes no email unless you offer one, there is
          no analytics script in the bundle, and robots.txt refuses the AI
          training crawlers. */}
      <p className="why-account__catch">
        No email needed — add one only if you want to be able to reset a
        forgotten password. Nothing is sold, no adverts, no tracking, and you
        can export or delete everything from{" "}
        <Link href="/settings">Settings</Link> whenever you like.
      </p>
    </section>
  )
}
