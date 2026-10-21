import { fetchBackground } from "./net"

/** Record a deliberate recovery action for a search already logged by the API. */
export function recordSearchFeedback(action: string, search: string): void {
  if (!search) return
  const fd = new FormData()
  fd.append("path", "/search")
  fd.append("kind", action === "missing" ? "missing" : "recover")
  if (action !== "missing") fd.append("action", action)
  fd.append("search", search)
  fetchBackground("/api/traffic/hit", { method: "POST", body: fd, keepalive: true })
}
