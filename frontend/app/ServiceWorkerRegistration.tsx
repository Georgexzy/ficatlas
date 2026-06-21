"use client"
import { useEffect } from "react"

// Registers the service worker that caches the app shell for offline use.
// Silently no-ops if the browser doesn't support service workers.
export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return
    // Register after load so it doesn't compete with first paint.
    const onLoad = () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {})
    }
    if (document.readyState === "complete") onLoad()
    else window.addEventListener("load", onLoad)
    return () => window.removeEventListener("load", onLoad)
  }, [])
  return null
}
