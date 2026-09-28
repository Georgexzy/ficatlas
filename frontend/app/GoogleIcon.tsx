/**
 * Google's "G", for the sign-in buttons.
 *
 * Google's identity guidelines ask that the button carrying their name also
 * carries their mark, in its four colours, unmodified — a plain text link
 * saying "Sign in with Google" is the shape people have been taught to be
 * suspicious of, because it is what a phishing page produces when it cannot be
 * bothered. The mark is the part a reader recognises before they read anything.
 *
 * Inline SVG, for the same three reasons as app/CompassMark.tsx and
 * app/SiteIcon.tsx: no network request on a page that already has work to do,
 * nothing new for the CSP to allow, and it scales with the button's font-size
 * rather than needing a second asset per density.
 *
 * The four paths are Google's own artwork and are deliberately NOT recoloured,
 * not themed, and not given `currentColor` — unlike every other icon here. A
 * monochrome Google G is a modified Google G, which is exactly what their
 * guidelines do not permit, and a reader checking whether a sign-in button is
 * genuine is checking those colours.
 *
 * aria-hidden: the button's own text says "Sign in with Google", so announcing
 * the mark as well would say Google twice.
 */
export default function GoogleIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={`google-icon ${className}`} viewBox="0 0 48 48"
         width="1em" height="1em" aria-hidden="true" focusable="false">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}
