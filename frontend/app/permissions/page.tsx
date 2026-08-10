"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import BackLink from "../BackLink"
import SiteHeader from "../SiteHeader"

// The other side of the takedown form.
//
// Someone here is doing FicAtlas a favour, not asking it for something, and the
// page should read that way. It also has to be honest that this is the only
// route by which permission can be given at all: a form that just says "I'm the
// author, go ahead" is worth nothing, because anyone can type anyone's name.
//
// Three steps, one screen. Nothing is stored until the last one succeeds.
type Step = "who" | "prove" | "done"

// Worded so the thing being consented to is unmissable.
//
// "Host my work here" is what this used to say, and it is the kind of phrase
// that can be read as "appear on your site" — which is what a listing already
// does, without anyone's permission. The actual request is to keep a copy of the
// complete text of someone's story on this server and serve it to readers. That
// is a materially different thing to agree to, it is the ONLY option here that
// requires proof, and it is the whole reason proof exists. So it says so, in the
// label rather than the small print.
const POLICIES = [
  { id: "host", label: "Store the full text of my stories on FicAtlas",
    detail: "A complete copy of each story is kept on FicAtlas and can be read here, in the app, without going to the archive. Your work still links back to where you posted it. This is the only option that needs you to verify." },
  { id: "metadata_only", label: "List my work, but never store the text",
    detail: "Title, summary and tags only, with a link out to the archive. No copy of the writing itself is kept." },
  { id: "deny", label: "Don't index my work at all",
    detail: "Your works are removed from the index entirely and not added again." },
]

export default function PermissionsPage() {
  const [step, setStep] = useState<Step>("who")
  const [site, setSite] = useState("ao3")
  const [author, setAuthor] = useState("")
  const [policy, setPolicy] = useState("host")
  const [email, setEmail] = useState("")
  const [token, setToken] = useState("")
  const [profileUrl, setProfileUrl] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<any>(null)

  // Coming from "Is this your work?" on a story page, the archive and pen name
  // are already known.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    if (q.get("site")) setSite(q.get("site")!)
    if (q.get("author")) setAuthor(q.get("author")!)
  }, [])

  const start = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      const fd = new FormData()
      fd.append("site", site); fd.append("author", author.trim())
      const r = await fetch("/api/permissions/challenge", { method: "POST", body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not start verification.")
      setToken(d.token); setProfileUrl(d.profile_url); setStep("prove")
    } catch (err: any) { setError(err.message) }
    finally { setBusy(false) }
  }

  const check = async () => {
    setBusy(true); setError(null)
    try {
      const fd = new FormData()
      fd.append("token", token); fd.append("policy", policy); fd.append("email", email)
      const r = await fetch("/api/permissions/verify", { method: "POST", body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not verify.")
      setResult(d); setStep("done")
    } catch (err: any) { setError(err.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="page-prose">
      <SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />

      <h1>Your work, your call</h1>
      <p>
        FicAtlas indexes ~19.9 million works as a listing — title, summary, tags
        and a link out. For a few thousand it also{" "}
        <strong>keeps a complete copy of the text</strong> so it can be read here
        without leaving the site. This page is where you decide which of those,
        if either, may happen to yours.
      </p>
      <p>
        Whatever you choose applies to everything you have already posted{" "}
        <strong>and everything you post later</strong>, so it is a once-only
        thing.
      </p>

      {/* Said plainly and early, because it is the question a reader of this page
          is actually asking, and because the answer is genuinely reassuring. */}
      <div className="perm-note">
        <p>
          <strong>You never need this to have something removed.</strong> If you
          want your work taken down, use the{" "}
          <Link href="/takedown">removal form</Link> — it asks for no proof, the
          text comes down straight away, and nothing is deleted so a mistake can
          always be undone.
        </p>
        <p>
          Proof is only needed for the opposite: saying <em>yes</em>. Anyone can
          type an author&apos;s name into a form, so permission that has not been
          verified would not be worth anything — least of all to you.
        </p>
      </div>

      {step === "who" && (
        <form onSubmit={start} className="takedown-form">
          {/* The choice comes BEFORE the username, not after.
              It used to appear only on the second screen, so someone entered
              their name having been told nothing about what they were agreeing
              to — and the option that needs proof is the one that puts a copy of
              their writing on someone else's server. Deciding first, then
              proving, is the honest order; it also means anyone who wants a
              restriction can see immediately that it asks nothing of them. */}
          <fieldset className="perm-choices">
            <legend>What would you like FicAtlas to do with your work?</legend>
            {POLICIES.map(pol => (
              <label key={pol.id} className={`perm-choice ${policy === pol.id ? "is-on" : ""}`}>
                <input type="radio" name="policy-first" value={pol.id}
                  checked={policy === pol.id} onChange={() => setPolicy(pol.id)} />
                <span>
                  <strong>{pol.label}</strong>
                  <em>{pol.detail}</em>
                </span>
              </label>
            ))}
          </fieldset>

          {policy !== "host" && (
            <p className="perm-shortcut">
              That one needs no proof at all — you can{" "}
              <Link href="/permissions/manage">set it straight away</Link> without
              verifying anything.
            </p>
          )}

          {/* What verifying is, before they start it rather than after.
              "Verify your account" sounds like a login, and the thing people
              reasonably fear is being asked for archive credentials — which AO3
              itself warns its users never to give a third-party app. Saying what
              this is NOT is the part that reassures; saying what it is takes one
              sentence. */}
          {policy === "host" && (
            <div className="perm-explain">
              <p className="perm-explain__head">What verifying involves</p>
              <p>
                You paste a short code into your own archive profile, we read
                that public page once and see it there, and that is the whole of
                it. It proves you can edit that profile, which only its owner can.
              </p>
              <ul>
                <li><strong>No password.</strong> You are never asked for your
                  archive login, and there is nothing to sign in to. AO3 tells its
                  users never to give a third-party app their password, and this
                  does not ask you to.</li>
                <li><strong>No access to your account.</strong> We cannot post,
                  edit, read your drafts, or see anything that is not already on
                  your public profile page.</li>
                <li><strong>One request.</strong> We fetch that page once, when
                  you press check. Not on a schedule, not afterwards.</li>
                <li><strong>The code is temporary.</strong> Delete it as soon as
                  the check passes — it is only read that once.</li>
              </ul>
            </div>
          )}

          <label>
            <span>Which archive</span>
            <select value={site} onChange={e => setSite(e.target.value)}>
              <option value="ao3">Archive of Our Own</option>
              <option value="ffnet">FanFiction.net</option>
            </select>
          </label>
          <label>
            <span>{site === "ao3" ? "Your AO3 username" : "Your FanFiction.net profile id or link"}</span>
            <input value={author} onChange={e => setAuthor(e.target.value)} required
              placeholder={site === "ao3" ? "exactly as it appears on the archive"
                                          : "e.g. 1234567, or paste your profile link"} />
          </label>
          {error && <p className="takedown-error">{error}</p>}
          <button className="btn btn--primary" disabled={busy || !author.trim()}>
            {busy ? "Starting…" : "Continue"}
          </button>
        </form>
      )}

      {step === "prove" && (
        <>
          <h2>Show us it&apos;s your account</h2>
          {/* Numbered, because "put this somewhere and come back" leaves people
              guessing where, whether it matters what else is in the field, and
              what happens to the code afterwards. Each step says what to do and
              nothing else; the reasoning is above, on the previous screen. */}
          <ol className="perm-steps">
            <li>
              Open{" "}
              <a href={profileUrl} target="_blank" rel="noopener noreferrer">
                your profile
              </a>{" "}
              and choose Edit.
            </li>
            <li>
              Paste the code below <strong>anywhere</strong> in the bio or
              &ldquo;About Me&rdquo; box. It can sit on its own line, among
              whatever is already there — nothing else needs changing.
            </li>
            <li>Save your profile.</li>
            <li>Come back here and press <strong>check</strong>.</li>
            <li>
              Delete the code from your profile. It is read once and never again.
            </li>
          </ol>
          {/* Neither site has a login we could use — AO3 has no public API and
              has told its users never to give a third-party app their password.
              Editing your own profile is proof we can check without ever asking
              for a credential. */}
          <p className="perm-token"><code>{token}</code></p>
          {/* What is kept, said before the button rather than in a policy page
              nobody opens. A record of consent that cannot be shown afterwards
              is not much use, so something is stored — and the author should
              know what, given they are the subject of it. */}
          <p className="perm-why">
            When the check passes we record: your archive and username, the
            choice below, the code, the address of the page it was found on, and
            a short snippet of the surrounding text — so the permission can be
            shown to have been given, rather than merely asserted. No password,
            no email unless you add one below.
          </p>

          {/* Repeated here, not merely carried forward. This is the moment the
              permission is actually recorded, and the reader should be able to
              see what it says without going back a screen. */}
          <fieldset className="perm-choices">
            <legend>Confirm what you are allowing</legend>
            {POLICIES.map(p => (
              <label key={p.id} className={`perm-choice ${policy === p.id ? "is-on" : ""}`}>
                <input type="radio" name="policy" value={p.id}
                  checked={policy === p.id} onChange={() => setPolicy(p.id)} />
                <span>
                  <strong>{p.label}</strong>
                  <em>{p.detail}</em>
                </span>
              </label>
            ))}
          </fieldset>

          <label className="perm-email">
            <span>Email (optional)</span>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="Only so we can reach you if something changes" />
          </label>

          {error && <p className="takedown-error">{error}</p>}
          <div className="btn-row">
            <button className="btn btn--primary" onClick={check} disabled={busy}>
              {busy ? "Checking your profile…" : "I've added it — check now"}
            </button>
            <button className="btn btn--ghost" onClick={() => setStep("who")} disabled={busy}>
              Back
            </button>
          </div>
        </>
      )}

      {step === "done" && result && (
        <div className="takedown-done">
          <h2>Recorded — thank you</h2>
          <p>
            We have you as <strong>{result.author_display || result.author}</strong>{" "}
            on {result.site === "ao3" ? "AO3" : "FanFiction.net"}, and your choice
            applies to your whole back catalogue and to anything you post from now on.
          </p>
          <p>
            You can change or withdraw this whenever you like, and withdrawing does
            not need proof — the same as removal never does.
          </p>
          <p>
            <Link href={`/permissions/manage?site=${result.site}&author=${encodeURIComponent(result.author_display || result.author)}`}
              className="btn btn--primary">Manage my works</Link>{" "}
            <Link href="/" className="btn btn--ghost">Back to search</Link>
          </p>
        </div>
      )}

      {/* Reachable without verifying, because looking at what a site holds about
          you — and taking it down — never needs proof here. */}
      {step === "who" && (
        <p className="perm-manage-link">
          Already told us, or just want to see what FicAtlas holds under your
          name? <Link href="/permissions/manage">Manage your works</Link>.
        </p>
      )}
    </div>
  )
}
