import { pollJob } from "./pollJob"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "./net"

export interface ImportedStory {
  id: string
  title: string
  chapters: number
}

/** Start a durable import job and wait for its result without holding the
 *  request to FicHub/AO3 open through the app's proxy chain. */
export async function importToLibrary(
  url: string,
  onProgress?: (message: string) => void,
): Promise<ImportedStory> {
  const form = new FormData()
  form.append("url", url)
  const started = await fetchWithTimeout("/api/library/import-url-job", {
    method: "POST", body: form,
  }, USER_TIMEOUT_MS)
  if (!started.ok) {
    let message = `Could not start the import (HTTP ${started.status}).`
    try {
      const body = await started.json()
      if (typeof body?.detail === "string") message = body.detail
    } catch { /* proxy errors are not always JSON */ }
    throw new Error(message)
  }

  const { job_id: jobId } = await started.json()
  if (!jobId) throw new Error("The import did not return a job id. Please try again.")
  const state = await pollJob(jobId, {
    endpoint: `/api/library/import-url-jobs/${encodeURIComponent(jobId)}`,
    timeoutMs: 10 * 60_000,
    intervalMs: 1500,
    onProgress: (job) => {
      if (typeof job.progress === "string") onProgress?.(job.progress)
    },
  })
  if (state.status === "error") {
    throw new Error(typeof state.error === "string" && state.error
      ? state.error : "The import failed. Please try again.")
  }
  const result = state.result as ImportedStory | undefined
  if (!result?.id) throw new Error("The import finished without a readable story. Please try again.")
  return result
}
