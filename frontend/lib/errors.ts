// Turning a failure into something a reader can act on.
//
// Every fetch on the site used to fail in one of two ways: silently (`.catch(()
// => {})`, of which there were dozens) or by printing the raw exception —
// "Search error: Failed to fetch", which is what the browser says when it cannot
// open a socket and means nothing to anybody. Neither tells a reader the one
// thing they need: is this me, is this the site, and is it worth trying again.
//
// So failures are classified, and the classification decides both the wording
// and whether a retry is even offered. Telling someone to "try again" when their
// wifi is off is as useless as saying nothing.

export type FailureKind =
  | "offline"      // this device has no connection
  | "unreachable"  // we have a connection; the server did not answer
  | "timeout"      // it answered too slowly, or not before we gave up
  | "server"       // it answered with 5xx — the fault is ours, not theirs
  | "denied"       // 401/403 — signed out, or not allowed
  | "notfound"     // 404
  | "ratelimit"    // 429
  | "unknown"

export interface Failure {
  kind: FailureKind
  /** One sentence, addressed to a reader, in plain words. */
  message: string
  /** Whether trying the same thing again could plausibly work. */
  retryable: boolean
  /** Kept for the console and bug reports, never shown as the main message. */
  detail?: string
}

const MESSAGES: Record<FailureKind, { message: string; retryable: boolean }> = {
  offline: {
    message: "You are offline. Stories you have saved to this device are still readable from your Library.",
    retryable: false,
  },
  unreachable: {
    message: "FicAtlas is not responding. It may be restarting — this usually clears in a minute.",
    retryable: true,
  },
  timeout: {
    message: "That took too long and was given up on. The index is large and some searches are slow; a narrower search will usually come back faster.",
    retryable: true,
  },
  server: {
    message: "Something went wrong at our end. This is a fault here, not with what you asked for.",
    retryable: true,
  },
  denied: {
    message: "You are not signed in, or that is not yours to see. Signing in again may fix it.",
    retryable: false,
  },
  notfound: {
    message: "That is not here. It may have been removed at the author's request.",
    retryable: false,
  },
  ratelimit: {
    message: "Too many requests at once. Wait a moment and try again.",
    retryable: true,
  },
  unknown: {
    message: "Something went wrong. Trying again often works.",
    retryable: true,
  },
}

/** Classify anything a fetch can throw or return. */
export function describeError(e: unknown, status?: number): Failure {
  // navigator.onLine is unreliable in one direction only: it says false when
  // there is definitely no connection, and true when there merely might be. So
  // it is trusted for "offline" and never for "online".
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return { ...MESSAGES.offline, kind: "offline" }
  }

  if (typeof status === "number") {
    if (status === 401 || status === 403) return { ...MESSAGES.denied, kind: "denied", detail: `HTTP ${status}` }
    if (status === 404) return { ...MESSAGES.notfound, kind: "notfound", detail: "HTTP 404" }
    if (status === 429) return { ...MESSAGES.ratelimit, kind: "ratelimit", detail: "HTTP 429" }
    if (status === 504 || status === 408) return { ...MESSAGES.timeout, kind: "timeout", detail: `HTTP ${status}` }
    if (status >= 500) return { ...MESSAGES.server, kind: "server", detail: `HTTP ${status}` }
  }

  const name = (e as any)?.name
  const msg = String((e as any)?.message ?? e ?? "")
  if (name === "AbortError" || /timeout|timed out/i.test(msg)) {
    return { ...MESSAGES.timeout, kind: "timeout", detail: msg }
  }
  // What fetch throws when it cannot reach the host at all. The wording differs
  // per browser — "Failed to fetch", "NetworkError when attempting to fetch
  // resource", "Load failed" on Safari — so all three are matched rather than
  // relying on one.
  if (name === "TypeError" || /failed to fetch|networkerror|load failed/i.test(msg)) {
    return { ...MESSAGES.unreachable, kind: "unreachable", detail: msg }
  }
  return { ...MESSAGES.unknown, kind: "unknown", detail: msg || undefined }
}

/** Fetch that fails loudly and in the same vocabulary everywhere.
 *  Throws a Failure, not an Error, so callers get the classification for free. */
export async function fetchOrFail(
  input: string, init?: RequestInit & { timeoutMs?: number },
): Promise<Response> {
  const { timeoutMs = 45_000, signal: external, ...rest } = init ?? {}
  const ctl = new AbortController()
  // A request with no timeout does not fail, it hangs — and a spinner that
  // never resolves is the worst of the failure modes, because nothing tells the
  // reader to stop waiting.
  const timer = setTimeout(
    () => ctl.abort(new DOMException("timeout", "TimeoutError")), timeoutMs)

  // The caller's signal and our timeout both have to be able to abort this one
  // request, and AbortSignal.any is too new to rely on. Relaying keeps a
  // component's unmount (or a superseded load) cancelling work that is genuinely
  // in flight, rather than merely ignoring its result — which on this index
  // means a 19.7M-row query keeps running for nobody.
  const relay = () => ctl.abort()
  if (external) {
    if (external.aborted) ctl.abort()
    else external.addEventListener("abort", relay)
  }

  try {
    const r = await fetch(input, { ...rest, signal: ctl.signal })
    if (!r.ok) throw describeError(null, r.status)
    return r
  } catch (e) {
    if (e && typeof e === "object" && "kind" in e) throw e as Failure
    throw describeError(e)
  } finally {
    clearTimeout(timer)
    external?.removeEventListener("abort", relay)
  }
}

/** `fetchOrFail` + `.json()`, which is what almost every call site actually
 *  wants. Kept separate so a caller needing the Response (headers, blob) still
 *  has one. */
export async function fetchJson<T = any>(
  input: string, init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const r = await fetchOrFail(input, init)
  return r.json() as Promise<T>
}

/** True when a rejection is just a cancelled request — a component unmounting or
 *  a superseded load — and therefore must not be shown to anyone as an error. */
export function isAbort(e: unknown): boolean {
  const name = (e as any)?.name
  if (name === "AbortError") return true
  // fetchOrFail rethrows a classified Failure, so the original name is gone;
  // an aborted request that was not our timeout carries no useful detail.
  return (e as any)?.kind === "unknown" && /aborted/i.test(String((e as any)?.detail ?? ""))
}
