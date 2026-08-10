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

const POLICIES = [
  { id: "host", label: "Host my work here",
    detail: "FicAtlas may store the full text so people can read it in the app. Your work still links back to the archive you posted it on." },
  { id: "metadata_only", label: "List it, but don't store the text",
    detail: "FicAtlas may show the title, summary and tags, and link out to the archive — but will never keep a copy of the writing itself." },
  { id: "deny", label: "Don't index my work at all",
    detail: "FicAtlas will remove your works from the index and not add them again." },
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
        If you write on AO3 or FanFiction.net, you can tell FicAtlas what it may
        do with your work — and it will apply to everything you have already
        posted <strong>and everything you post later</strong>, so this is a
        once-only thing.
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
          <p>
            Put this code anywhere in your{" "}
            <a href={profileUrl} target="_blank" rel="noopener noreferrer">profile</a>,
            save it, then come back and press check. You can delete the code
            again straight afterwards.
          </p>
          {/* Neither site has a login we could use — AO3 has no public API and
              has told its users never to give a third-party app their password.
              Editing your own profile is proof we can check without ever asking
              for a credential. */}
          <p className="perm-token"><code>{token}</code></p>
          <p className="perm-why">
            Neither archive offers a way for other sites to log you in, and AO3
            asks its users never to give their password to a third-party app —
            so this is how we check without one.
          </p>

          <fieldset className="perm-choices">
            <legend>What are you allowing?</legend>
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
          <p><Link href="/" className="btn btn--primary">Back to search</Link></p>
        </div>
      )}
    </div>
  )
}
