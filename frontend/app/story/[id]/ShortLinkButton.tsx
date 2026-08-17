"use client"
import { useState } from "react"
import { encodeStoryId } from "@/lib/shortId"

// "Copy link", giving out the short form.
//
// The route (/s/<code>) existed before anything offered it, which makes it
// plumbing rather than a feature — nobody types a base64url code by hand. This
// is the only place the short form is produced for a human.
//
// Its own client component rather than a addition to StoryClient: that file is
// ~2,700 lines and fetches the whole story, and this needs one string and a
// clipboard call. Rendered from the server page beside the hub links.
export default function ShortLinkButton({ id }: { id: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  const code = encodeStoryId(id)
  // Nothing to shorten if the id is not a uuid — render nothing rather than a
  // button that copies a broken link.
  if (!code) return null

  const copy = async () => {
    const url = `${window.location.origin}/s/${code}`
    try {
      await navigator.clipboard.writeText(url)
      setState("copied")
      setTimeout(() => setState("idle"), 2000)
    } catch {
      // Clipboard access is refused outside a secure context and in some
      // embedded browsers. Say so rather than silently doing nothing — the
      // reader can still copy from the address bar.
      setState("failed")
      setTimeout(() => setState("idle"), 3000)
    }
  }

  return (
    <button type="button" onClick={copy} className="short-link"
      title={`Copy a short link to this story (/s/${code})`}>
      {state === "copied" ? "✓ Copied"
        : state === "failed" ? "Couldn’t copy — use the address bar"
        : "🔗 Copy short link"}
    </button>
  )
}
