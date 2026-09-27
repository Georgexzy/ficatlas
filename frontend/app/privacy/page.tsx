import Link from "next/link"
import SiteHeader from "../SiteHeader"

export const metadata = {
  title: "Privacy",   // layout.tsx appends " · FicAtlas"
  description:
    "What FicAtlas records, what it deliberately cannot know, how long it "
    + "keeps anything, and how to have your account and data deleted.",
  alternates: { canonical: "/privacy" },
}

// Required before Google will publish an OAuth consent screen, and worth
// having on its own terms: a site that asks people to make an account owes
// them a plain account of what it stores.
//
// Written from the code rather than from a template. Every claim below is
// checkable in this repository — the visitor hash in backend/tracking.py, the
// retention sweep beside it, the session cookie in backend/api/auth.py — and
// that is the point. A privacy policy describing something the software does
// not do is worse than none, because it is a promise nobody is keeping.
export default function Privacy() {
  return (
    <div className="page-prose">
      <SiteHeader />
      <h1>Privacy</h1>

      <p>
        FicAtlas is a search engine for fanfiction. It is run by one person and
        it sells nothing, so there is no commercial reason to know who you are —
        and the parts that count visitors are built so that it mostly cannot.
      </p>

      <h2>If you never make an account</h2>
      <p>
        Nothing that identifies you is stored. When you load a page or run a
        search, the server records the page or the query, the time, whether a
        result was clicked through to an archive, and a <em>visitor hash</em>.
      </p>
      <p>
        That hash is the whole of how visitors are counted, and it is
        deliberately weak. It is a keyed hash of the current <strong>date</strong>,
        your IP address and your browser&rsquo;s user-agent string. Because the
        date is part of it, the same person visiting on two days produces two
        unrelated hashes, and nothing can join them. Your IP address itself is
        never written down.
      </p>
      <p>
        The practical effect: this site can tell how many people searched for
        something yesterday, and cannot tell whether any of them came back
        today. That is a deliberate trade — it gives up the ability to measure
        loyal readers in exchange for not building a profile of anybody.
      </p>
      <p>
        Search queries are recorded, because they are what the index is judged
        by. They are stored next to the hash described above and not next to any
        name, address or IP.
      </p>

      <h2>How long any of it is kept</h2>
      <p>
        Ninety days, then it is deleted automatically. There is no archive of
        older traffic.
      </p>

      <h2>If you do make an account</h2>
      {/* "and only this" makes this list a PROMISE, so it has to be complete,
          and the first version was not. It said an email address was used "for
          signing in" (you sign in with a username; the address is optional and
          only ever used for a reset), described follows as "fandoms and
          pairings" (you follow WORKS), and omitted session records — which
          include the browser string of each device — along with everything the
          reader's own data actually covers.

          Checked against the schema rather than written from memory:
          models/user.py for User and UserSession, api/userdata.py ALLOWED_KEYS
          and lib/storageKeys.ts for the synced data, api/follows.py for follows.
          Re-check it against those four whenever any of them changes: an
          incomplete list under "and only this" is not a rough edge in a privacy
          page, it is a false statement in the one document that exists to be
          exact. */}
      <p>Then the following is stored, and only this:</p>
      <ul>
        <li>a username you choose, and the date the account was made;</li>
        <li>
          an email address <em>only if you give one</em> — it is optional, you
          sign in with your username, and the address is used for nothing but
          sending yourself a password reset;
        </li>
        <li>
          a password <em>hash</em> — never the password, so nobody here can read
          or recover it. An account made with Google has no password at all
          unless you add one;
        </li>
        <li>
          if you sign in with Google, the account identifier Google returns.
          Google tells this site your email address and nothing else — not your
          contacts, your files, or anything in your Google account;
        </li>
        <li>
          whether the account is a reader, an administrator or the owner;
        </li>
        <li>
          one record per signed-in device, so you can see them and sign them out
          from your account page: when it signed in, when it was last used, when
          it expires, and the browser-and-platform string your browser sends
          (&ldquo;Chrome on Android&rdquo;). No IP address;
        </li>
        <li>
          the reading data you build up: the works you follow, your bookmarks,
          your place in each story, the searches you save and the ones you ran
          recently, which works you keep offline, and your reader and search
          preferences.
        </li>
      </ul>
      <p>
        That last item includes your never-show-me list — the ships, tropes,
        fandoms, characters and authors you have ruled out of every search. It is
        called out separately because it is the most revealing thing here: a
        standing list of what somebody refuses to read says more than any single
        search does. It is stored so that it follows you between devices, it is
        used for nothing but filtering your own results, it is never shown to
        anyone else, and it goes when the account goes.
      </p>
      <p>
        One cookie is set, holding your session so you stay signed in. There are
        no advertising or tracking cookies, because there is no advertising.
      </p>

      <h2>Other companies involved</h2>
      <ul>
        <li>
          <strong>Cloudflare</strong> serves the site and sees requests in
          transit, as any host would.
        </li>
        <li>
          <strong>Google Fonts</strong> serves two typefaces, so your browser
          fetches those files from Google.
        </li>
        <li>
          <strong>Google</strong>, only if you choose to sign in with it.
        </li>
      </ul>
      <p>
        There is no analytics provider, no advertising network, and no data sold
        or shared with anyone. The traffic counting described above is this
        site&rsquo;s own, on its own server.
      </p>

      <h2>Fanworks shown here</h2>
      <p>
        FicAtlas indexes metadata about works published on other archives —
        titles, summaries, tags, and links back to them. It is not the
        publisher. If you are an author and want your work removed from the
        index, that is your call and it will be honoured: see{" "}
        <Link href="/about">About &amp; contact</Link> for the takedown route.
      </p>

      <h2>Deleting your account</h2>
      <p>
        You can delete your account from{" "}
        <Link href="/settings?tab=account">your account settings</Link>, which removes it and
        everything saved against it. If anything there does not work, email{" "}
        <a href="mailto:help@ficatlas.com">help@ficatlas.com</a> and it will be
        done by hand.
      </p>

      <h2>Changes</h2>
      <p>
        If what is collected ever changes, this page changes with it. It
        describes how the software actually behaves rather than what a policy
        template would allow.
      </p>
    </div>
  )
}
