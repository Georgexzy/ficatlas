"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import BackLink from "../../BackLink"
import SiteHeader from "../../SiteHeader"

// What FicAtlas holds under your name, and what you can do about it.
//
// Deliberately usable WITHOUT verifying. Everything on this page is either
// already public on the author's own results page, or is a removal — and
// removal never requires proof anywhere in this system. Asking someone to prove
// who they are before they may look at what a site holds about them, or take it
// down, would be the wrong way round.
//
// Verification only unlocks the one thing that grants rather than restricts:
// setting the standing policy to "host".
interface Work {
  id: string
  title: string
  url: string
  is_hosted: boolean
  text_withdrawn: boolean
  delisted: boolean
  word_count: number
  chapter_count: number
}

const POLICY_LABEL: Record<string, string> = {
  host: "FicAtlas may host the full text of your work",
  metadata_only: "Listed and linked, but the text is never stored here",
  deny: "Not indexed at all",
}

export default function ManageWorksPage() {
  const [site, setSite] = useState("ao3")
  const [author, setAuthor] = useState("")
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    if (q.get("site")) setSite(q.get("site")!)
    if (q.get("author")) setAuthor(q.get("author")!)
  }, [])

  const load = useCallback(async (s: string, a: string) => {
    if (!a.trim()) return
    setBusy(true); setError(null); setMsg(null)
    try {
      const r = await fetch(`/api/permissions/works?site=${s}&author=${encodeURIComponent(a.trim())}`)
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not load your works.")
      setData(d)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }, [])

  // Auto-load when arrived from a link that already knows who you are.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    if (q.get("author")) load(q.get("site") || "ao3", q.get("author")!)
  }, [load])

  const withdraw = async (w: Work, delist: boolean) => {
    const what = delist
      ? `Remove the listing for "${w.title}" as well as its text?`
      : `Take down the text of "${w.title}"?`
    if (!confirm(`${what}\n\nNothing is deleted — this can be undone.`)) return
    setBusy(true); setError(null)
    try {
      const fd = new FormData(); fd.append("delist", String(delist))
      const r = await fetch(`/api/permissions/works/${w.id}/withdraw`, { method: "POST", body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not take that down.")
      setMsg(`"${w.title}" is down.`)
      load(data.site, data.author)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  const restrict = async (policy: string) => {
    if (!confirm(policy === "deny"
      ? "Stop FicAtlas indexing your work entirely?"
      : "Keep the listings but never store your text here?")) return
    setBusy(true); setError(null)
    try {
      const fd = new FormData()
      fd.append("site", data.site); fd.append("author", data.author); fd.append("policy", policy)
      const r = await fetch("/api/permissions/restrict", { method: "POST", body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not save that.")
      setMsg("Saved. This applies to everything you post from now on too.")
      load(data.site, data.author)
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="page-prose">
      <SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />
      <h1>Your works on FicAtlas</h1>

      <form className="takedown-form" onSubmit={e => { e.preventDefault(); load(site, author) }}>
        <label>
          <span>Archive</span>
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
          {busy ? "Looking…" : "Show my works"}
        </button>
      </form>

      {error && <p className="takedown-error">{error}</p>}
      {msg && <p className="perm-ok">{msg}</p>}

      {data && (
        <>
          <div className="perm-note">
            <p>
              <strong>{data.count}</strong> work{data.count === 1 ? "" : "s"} indexed
              under <strong>{data.author}</strong>.{" "}
              {data.verified
                ? <>Your account is verified, and your standing choice is:{" "}
                    <strong>{POLICY_LABEL[data.policy] ?? data.policy}</strong>.</>
                : <>This account is not verified. You can still take anything down
                    from here — removal never needs proof — but{" "}
                    <Link href={`/permissions?site=${data.site}&author=${encodeURIComponent(data.author)}`}>
                      verifying</Link>{" "}is the only way to let FicAtlas host your text.</>}
            </p>
          </div>

          <div className="btn-row perm-bulk">
            <button className="btn btn--ghost" disabled={busy}
              onClick={() => restrict("metadata_only")}>
              Never store my text here
            </button>
            <button className="btn btn--ghost" disabled={busy}
              onClick={() => restrict("deny")}>
              Don&apos;t index me at all
            </button>
          </div>

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
                    {/* Only offered where there is something to take down. A
                        button that does nothing is worse than no button. */}
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
            back if you change your mind or it was a mistake. For anything this
            page cannot do, the <Link href="/takedown">removal form</Link> reaches
            a person.
          </p>
        </>
      )}
    </div>
  )
}
