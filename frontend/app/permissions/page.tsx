"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import BackLink from "../BackLink"
import SiteHeader from "../SiteHeader"

// One page for an author, rather than two.
//
// This was split across /permissions (choose and verify) and /permissions/manage
// (see your works and restrict), and the split did not survive contact: both set
// a policy, both explained the same asymmetry, and an author who wanted to look
// before deciding had to guess which door was theirs. The order below is the one
// people actually want — who are you, here is what we hold, now decide, and prove
// it only if you are granting something.
//
// /permissions/manage still exists as a redirect, because it is linked from
// About, the takedown form and the story page footer.
type Step = "who" | "review" | "prove" | "done"

interface Work {
  id: string; title: string; url: string
  is_hosted: boolean; text_withdrawn: boolean; delisted: boolean
  word_count: number; chapter_count: number
}

// Worded so the thing being consented to is unmissable. "Host my work here" can
// be read as "appear on your site", which a listing already does for 19.9M works
// without anyone's permission; the actual request is to keep a complete copy of
// someone's story on this server.
const POLICIES = [
  { id: "host", label: "Store the full text of my stories on FicAtlas",
    detail: "A complete copy of each story is kept on FicAtlas and can be read here, in the app, without going to the archive. Your work still links back to where you posted it. This is the only option that needs you to verify." },
  { id: "metadata_only", label: "List my work, but never store the text",
    detail: "Title, summary and tags only, with a link out to the archive. No copy of the writing itself is kept." },
  { id: "deny", label: "Don't index my work at all",
    detail: "Your works are removed from the index entirely and not added again." },
]

const POLICY_LABEL: Record<string, string> = {
  host: "FicAtlas may store the full text of your work",
  metadata_only: "Listed and linked, but the text is never stored here",
  deny: "Not indexed at all",
}

export default function PermissionsPage() {
  const [step, setStep] = useState<Step>("who")
  const [site, setSite] = useState("ao3")
  const [author, setAuthor] = useState("")
  const [policy, setPolicy] = useState("host")
  const [email, setEmail] = useState("")
  const [token, setToken] = useState("")
  const [profileUrl, setProfileUrl] = useState("")
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const lookup = useCallback(async (s: string, a: string) => {
    if (!a.trim()) return
    setBusy(true); setError(null); setMsg(null)
    try {
      const r = await fetch(`/api/permissions/works?site=${s}&author=${encodeURIComponent(a.trim())}`)
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not look that up.")
      setData(d)
      if (d.policy) setPolicy(d.policy)
      setStep("review")
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }, [])

  // Arriving from a story page, About or the takedown form, which already know
  // who the author is.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    if (q.get("site")) setSite(q.get("site")!)
    if (q.get("author")) {
      setAuthor(q.get("author")!)
      lookup(q.get("site") || "ao3", q.get("author")!)
    }
  }, [lookup])

  const save = async () => {
    // Restrictions need no proof — see api/permissions.py for why. Only "host"
    // grants something, and only granting can licence writing that is not yours.
    if (policy === "host") { startVerification(); return }
    setBusy(true); setError(null)
    try {
      const fd = new FormData()
      fd.append("site", data.site); fd.append("author", data.author); fd.append("policy", policy)
      const r = await fetch("/api/permissions/restrict", { method: "POST", body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not save that.")
      setMsg("Saved. This applies to everything you post from now on too.")
      lookup(data.site, data.author)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const startVerification = async () => {
    setBusy(true); setError(null)
    try {
      const fd = new FormData()
      fd.append("site", data.site); fd.append("author", data.author)
      const r = await fetch("/api/permissions/challenge", { method: "POST", body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not start verification.")
      setToken(d.token); setProfileUrl(d.profile_url); setStep("prove")
    } catch (e: any) { setError(e.message) }
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
      setStep("done"); lookup(d.site, d.author)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const withdraw = async (w: Work, delist: boolean) => {
    if (!confirm(`${delist ? `Remove the listing for "${w.title}" as well as its text?`
                           : `Take down the text of "${w.title}"?`}\n\nNothing is deleted — this can be undone.`)) return
    setBusy(true); setError(null)
    try {
      const fd = new FormData(); fd.append("delist", String(delist))
      const r = await fetch(`/api/permissions/works/${w.id}/withdraw`, { method: "POST", body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not take that down.")
      setMsg(`"${w.title}" is down.`)
      lookup(data.site, data.author)
    } catch (e: any) { setError(e.message) }
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
        without leaving the site. This page is where you see what it holds under
        your name, and decide which of those may happen to yours.
      </p>

      <div className="perm-note">
        <p>
          <strong>You never need this to have something removed.</strong> Taking
          work down asks nothing of you — not here, and not on the{" "}
          <Link href="/takedown">removal form</Link>. Nothing is ever deleted, so
          a mistake can always be undone.
        </p>
        <p>
          Proof is only needed for the opposite: saying <em>yes</em>. Anyone can
          type an author&apos;s name into a form, so permission that has not been
          verified would not be worth anything — least of all to you.
        </p>
      </div>

      {error && <p className="takedown-error">{error}</p>}
      {msg && <p className="perm-ok">{msg}</p>}

      {step === "who" && (
        <form className="takedown-form" onSubmit={e => { e.preventDefault(); lookup(site, author) }}>
          <label>
            <span>Which archive</span>
            <select value={site} onChange={e => setSite(e.target.value)}>
              <option value="ao3">Archive of Our Own</option>
              <option value="ffnet">FanFiction.net</option>
            </select>
          </label>
          <label>
            <span>The name your work is published under</span>
            <input value={author} onChange={e => setAuthor(e.target.value)} required
              placeholder="exactly as it appears on your stories" />
          </label>
          <button className="btn btn--primary" disabled={busy || !author.trim()}>
            {busy ? "Looking…" : "Show what FicAtlas holds"}
          </button>
        </form>
      )}

      {(step === "review" || step === "done") && data && (
        <>
          <div className="perm-note">
            <p>
              <strong>{data.count}</strong> work{data.count === 1 ? "" : "s"} indexed
              under <strong>{data.author}</strong>.{" "}
              {data.verified
                ? <>Verified — your standing choice is:{" "}
                    <strong>{POLICY_LABEL[data.policy] ?? data.policy}</strong>.</>
                : <>This account is not verified, so FicAtlas will not store your
                    text. You can still change that below, or take anything down
                    without proving a thing.</>}
            </p>
          </div>

          <fieldset className="perm-choices">
            <legend>What would you like FicAtlas to do with your work?</legend>
            {POLICIES.map(pol => (
              <label key={pol.id} className={`perm-choice ${policy === pol.id ? "is-on" : ""}`}>
                <input type="radio" name="policy" value={pol.id}
                  checked={policy === pol.id} onChange={() => setPolicy(pol.id)} />
                <span><strong>{pol.label}</strong><em>{pol.detail}</em></span>
              </label>
            ))}
          </fieldset>

          <div className="btn-row perm-bulk">
            <button className="btn btn--primary" onClick={save} disabled={busy || policy === data.policy}>
              {busy ? "Saving…"
                : policy === data.policy ? "That is already your choice"
                : policy === "host" ? "Verify my account and allow this"
                : "Save this choice"}
            </button>
            <button className="btn btn--ghost" onClick={() => { setStep("who"); setData(null) }}>
              Different name
            </button>
          </div>
          {policy === "host" && !data.verified && (
            <p className="perm-shortcut">
              This is the one option that needs proof — you will be asked to put a
              short code in your own archive profile. No password, no account
              access.
            </p>
          )}

          {data.works.length === 0
            ? <p className="library-empty">Nothing is indexed under that name.</p>
            : (
              <ul className="perm-works">
                {data.works.map((w: Work) => (
                  <li key={w.id} className="perm-work">
                    <div className="perm-work__main">
                      <Link href={`/story/${w.id}`} className="perm-work__title">{w.title}</Link>
                      <p className="perm-work__meta">
                        {w.word_count?.toLocaleString()} words · {w.chapter_count} ch
                        {w.is_hosted && !w.text_withdrawn && <> · <strong>text stored here</strong></>}
                        {w.text_withdrawn && <> · text taken down</>}
                        {w.delisted && <> · delisted</>}
                      </p>
                    </div>
                    {!w.delisted && (
                      <div className="perm-work__actions">
                        {w.is_hosted && !w.text_withdrawn && (
                          <button className="card-btn" disabled={busy}
                            onClick={() => withdraw(w, false)}>Take text down</button>
                        )}
                        <button className="card-btn" disabled={busy}
                          onClick={() => withdraw(w, true)}>Remove listing</button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}

          <p className="perm-why">
            Nothing here is ever deleted, so anything you take down can be put
            back. For anything this page cannot do, the{" "}
            <Link href="/takedown">removal form</Link> reaches a person.
          </p>
        </>
      )}

      {step === "prove" && (
        <>
          <h2>Show us it&apos;s your account</h2>
          <div className="perm-explain">
            <p className="perm-explain__head">What verifying involves</p>
            <p>
              You paste a short code into your own archive profile, we read that
              public page once and see it there, and that is the whole of it. It
              proves you can edit that profile, which only its owner can.
            </p>
            <ul>
              <li><strong>No password.</strong> You are never asked for your archive
                login, and there is nothing to sign in to. AO3 tells its users never
                to give a third-party app their password, and this does not ask you to.</li>
              <li><strong>No access to your account.</strong> We cannot post, edit,
                read your drafts, or see anything not already on your public profile.</li>
              <li><strong>One request</strong>, when you press check. Not on a
                schedule, not afterwards.</li>
            </ul>
          </div>

          <ol className="perm-steps">
            <li>Open <a href={profileUrl} target="_blank" rel="noopener noreferrer">your profile</a> and choose Edit.</li>
            <li>Paste the code below <strong>anywhere</strong> in the bio or
              &ldquo;About Me&rdquo; box — nothing else needs changing.</li>
            <li>Save your profile.</li>
            <li>Come back here and press <strong>check</strong>.</li>
            <li>Delete the code. It is read once and never again.</li>
          </ol>

          <p className="perm-token"><code>{token}</code></p>
          <p className="perm-why">
            When the check passes we record: your archive and username, the choice
            you made, the code, the address of the page it was found on, and a
            short snippet of the surrounding text — so the permission can be shown
            to have been given rather than merely asserted. No password, no email
            unless you add one.
          </p>

          <label className="perm-email">
            <span>Email (optional)</span>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="Only so we can reach you if something changes" />
          </label>

          <div className="btn-row">
            <button className="btn btn--primary" onClick={check} disabled={busy}>
              {busy ? "Checking your profile…" : "I've added it — check now"}
            </button>
            <button className="btn btn--ghost" onClick={() => setStep("review")} disabled={busy}>
              Back
            </button>
          </div>
        </>
      )}
    </div>
  )
}
