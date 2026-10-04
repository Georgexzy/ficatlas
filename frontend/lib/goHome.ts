/**
 * "Take me to the front door", for the one case a link to `/` cannot do it.
 *
 * Some results are shown without changing the address — "Surprise me" puts
 * eight works on screen and the URL stays `/`. A link to `/` from there is a
 * link to the page you are on, so the wordmark and the Home tab did nothing at
 * all while the results sat there. Reported as "pressing FicAtlas doesn't go
 * home on the results page".
 *
 * When the click lands on bare `/`, this announces it and the search page
 * resets itself to the landing state. Anywhere else the link navigates as
 * normal and this does nothing.
 */
export const GO_HOME_EVENT = "ficatlas:go-home"

export function announceGoHome(): void {
  try {
    if (window.location.pathname === "/" && !window.location.search) {
      window.dispatchEvent(new Event(GO_HOME_EVENT))
    }
  } catch {}
}
