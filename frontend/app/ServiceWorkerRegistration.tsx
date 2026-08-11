"use client"
import { useEffect } from "react"

// Registers the service worker that caches the app shell for offline use, and —
// importantly — makes updates land without anyone needing to know what a hard
// refresh is.
//
// Registering once and stopping there left the app silently stale in a way that
// is genuinely hard to escape from an installed PWA, where there is no address
// bar and no reload button:
//
//   * a running page keeps executing the JS and CSS it booted with, even after a
//     newer worker has activated underneath it. Escaping that means closing the
//     app from the task switcher — backgrounding it is not enough.
//   * the browser only re-checks /sw.js on navigation, so an app left open for
//     days never notices a deploy at all.
//
// So: poll for a new worker periodically and whenever the app returns to the
// foreground, then reload once the replacement takes control.
export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return

    let registration: ServiceWorkerRegistration | undefined
    let refreshing = false
    let interval: ReturnType<typeof setInterval> | undefined

    // Fires when the new worker takes control. Reload so the page picks up the
    // assets it just installed. The guard matters because this can fire more
    // than once and would otherwise loop.
    const onControllerChange = () => {
      if (refreshing) return
      refreshing = true
      window.location.reload()
    }

    // update() re-requests /sw.js. It is served max-age=0, so this is a cheap
    // conditional request rather than a download.
    const checkForUpdate = () => { registration?.update().catch(() => {}) }

    const onVisible = () => {
      if (document.visibilityState === "visible") checkForUpdate()
    }

    // Ask for persistent storage as soon as an INSTALLED app starts.
    //
    // It used to be requested only when a story was saved, on the belief that
    // Safari did not implement it at all. WebKit does, and grants it on
    // heuristics that include being a Home Screen Web App — which is exactly
    // this case and exactly the platform that evicts most eagerly. Asking at
    // launch means the protection is already in place before there is anything
    // to protect, rather than being requested at the moment of the first save
    // and possibly refused.
    //
    // Only when actually installed: in a browser tab the request is far more
    // likely to be denied, and a denial is remembered.
    const askToPersist = () => {
      try {
        const installed = window.matchMedia?.("(display-mode: standalone)")?.matches
          || (navigator as any).standalone === true
        if (!installed || !navigator.storage?.persist) return
        navigator.storage.persisted?.().then(already => {
          if (!already) navigator.storage.persist().catch(() => {})
        }).catch(() => {})
      } catch { /* nothing to do; saving still asks again later */ }
    }

    const onLoad = () => {
      askToPersist()
      navigator.serviceWorker.register("/sw.js")
        .then(reg => {
          registration = reg
          navigator.serviceWorker.addEventListener("controllerchange", onControllerChange)
          // Reopening an installed PWA is when a stale app is most noticeable.
          document.addEventListener("visibilitychange", onVisible)
          // And a long-lived tab needs a nudge of its own.
          interval = setInterval(checkForUpdate, 30 * 60_000)
          checkForUpdate()
        })
        .catch(() => {})
    }

    if (document.readyState === "complete") onLoad()
    else window.addEventListener("load", onLoad)

    return () => {
      window.removeEventListener("load", onLoad)
      document.removeEventListener("visibilitychange", onVisible)
      navigator.serviceWorker.removeEventListener("controllerchange", onControllerChange)
      if (interval) clearInterval(interval)
    }
  }, [])

  return null
}
