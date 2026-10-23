import { fetchBackground } from "./net"

/** Record a recovery action, including an automatic interpretation attempt. */
export function recordSearchFeedback(action: string, search: string): void {
  if (!search) return
  const fd = new FormData()
  fd.append("path", "/search")
  fd.append("kind", action === "missing" ? "missing" : "recover")
  if (action !== "missing") fd.append("action", action)
  fd.append("search", search)
  fetchBackground("/api/traffic/hit", { method: "POST", body: fd, keepalive: true })
}
