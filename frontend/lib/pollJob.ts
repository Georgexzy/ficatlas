// Poll a background scrape job to completion.
//
// The library page had this loop written out three times, each as a bare
// `while (true)` with no deadline and no way to stop it. Two consequences:
//
//   * a job that never reached "done" or "error" — which was possible until
//     background tasks stopped being garbage-collectable mid-run — polled every
//     1.5s forever, for as long as the tab stayed open.
//   * navigating away did not stop it, so it kept calling setState on an
//     unmounted component.
//
// This gives every caller a deadline and an abort signal, and keeps the
// progress/done/error shape identical to what the endpoints return.

export interface JobState {
  status: "running" | "done" | "error"
  progress?: string
  pages_ok?: number
  pages_failed?: number
  found?: number
  newly_indexed?: number
  error?: string
  [k: string]: unknown
}

export interface PollOptions {
  /** Called whenever `progress` changes, for live status text. */
  onProgress?: (state: JobState) => void
  /** Stop polling after this long. Scrapes are minutes, not hours. */
  timeoutMs?: number
  intervalMs?: number
  signal?: AbortSignal
}

export class JobPollError extends Error {
  constructor(message: string, readonly kind: "http" | "timeout" | "aborted") {
    super(message)
  }
}

export async function pollJob(
  jobId: string,
  { onProgress, timeoutMs = 15 * 60_000, intervalMs = 1500, signal }: PollOptions = {},
): Promise<JobState> {
  const deadline = Date.now() + timeoutMs
  let lastProgress = ""

  while (true) {
    if (signal?.aborted) throw new JobPollError("cancelled", "aborted")
    if (Date.now() > deadline) {
      throw new JobPollError(
        `Gave up waiting after ${Math.round(timeoutMs / 60000)} minutes. ` +
        `The job may still be running on the server.`,
        "timeout",
      )
    }

    await new Promise(r => setTimeout(r, intervalMs))
    if (signal?.aborted) throw new JobPollError("cancelled", "aborted")

    let res: Response
    try {
      res = await fetch(`/api/library/jobs/${jobId}`, { signal })
    } catch (e: any) {
      if (signal?.aborted || e?.name === "AbortError") {
        throw new JobPollError("cancelled", "aborted")
      }
      throw new JobPollError(e?.message || "network error", "http")
    }
    if (!res.ok) {
      throw new JobPollError(`Lost track of job ${jobId} (HTTP ${res.status})`, "http")
    }

    const state: JobState = await res.json()
    if (state.progress && state.progress !== lastProgress) {
      lastProgress = state.progress
      onProgress?.(state)
    }
    if (state.status === "done" || state.status === "error") return state
  }
}
