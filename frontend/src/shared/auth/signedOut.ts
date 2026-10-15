/**
 * "Signed out" state for the whole app.
 *
 * In production the site sits behind oauth2-proxy (Google sign-in). When the
 * sign-in expires, the proxy answers API calls with HTTP 401 before they
 * reach the backend. That is not a server error and it does not mean the bots
 * are down, so the app flips into a "signed out" state instead:
 *   - a banner asks the user to sign in again,
 *   - react-query stops refetching (see installSignedOutQueryGuard),
 *   - the axios client and apiFetch() stop sending requests,
 *   - panels show "Signed out" instead of red error text.
 *
 * What we can detect:
 *   - HTTP 401 from any API call. The backend itself never sends 401, so a 401
 *     always comes from the sign-in proxy.
 *   - A response whose final URL (after redirects) is the proxy's sign-in page
 *     (/oauth2/sign_in or /oauth2/start).
 *   - An opaque redirect (fetch with redirect: 'manual').
 * What we cannot detect: a redirect to another origin (Google) shows up in the
 * browser as a plain network error with no status, which looks the same as a
 * real outage. To avoid that case, every API call says it wants JSON
 * (axios does this by default, apiFetch adds it): oauth2-proxy then answers
 * with 401 instead of redirecting to Google.
 */
import { create } from 'zustand'
import { onlineManager } from '@tanstack/react-query'

/** Short plain text shown wherever a request failed because of sign-in. */
export const SIGNED_OUT_TEXT = 'Signed out. Sign in again to see live data.'

interface SignedOutState {
  signedOut: boolean
}

const useSignedOutStore = create<SignedOutState>(() => ({ signedOut: false }))

export function isSignedOut(): boolean {
  return useSignedOutStore.getState().signedOut
}

export function markSignedOut(): void {
  if (!isSignedOut()) useSignedOutStore.setState({ signedOut: true })
}

/** For tests: go back to the signed-in state. */
export function resetSignedOut(): void {
  useSignedOutStore.setState({ signedOut: false })
}

/** React hook: true once any API call has failed because of sign-in. */
export function useSignedOut(): boolean {
  return useSignedOutStore(s => s.signedOut)
}

export function subscribeSignedOut(listener: (signedOut: boolean) => void): () => void {
  return useSignedOutStore.subscribe(s => listener(s.signedOut))
}

const SIGN_IN_PATH = /\/oauth2\/(sign_in|start)\b/

/** True when a URL points at the sign-in proxy's sign-in page. */
export function isSignInUrl(url: unknown): boolean {
  return typeof url === 'string' && SIGN_IN_PATH.test(url)
}

/**
 * Thrown (or returned as a rejection) when a request failed because the user
 * is signed out. It carries an axios-like `response` with status 401 and a
 * `detail` text, so the existing error readers (apiErrorDetail,
 * graphActionError, graphErrorDetail) show the plain signed-out text.
 */
export class SignedOutError extends Error {
  readonly signedOut = true
  readonly response = { status: 401, data: { detail: SIGNED_OUT_TEXT } }
  constructor() {
    super(SIGNED_OUT_TEXT)
    this.name = 'SignedOutError'
  }
}

/** True when an error came from a sign-in failure (401 or SignedOutError). */
export function isAuthError(e: unknown): boolean {
  if (e instanceof SignedOutError) return true
  if (!e || typeof e !== 'object') return false
  if ((e as { signedOut?: unknown }).signedOut === true) return true
  return (e as { response?: { status?: unknown } }).response?.status === 401
}

/**
 * Link to the proxy's sign-in page that comes back to the current page.
 * Always on the current origin: in dev (API on localhost) there is no proxy,
 * but the link is still a plain same-origin path.
 */
export function signInHref(loc: Pick<Location, 'pathname' | 'search'> = window.location): string {
  return `/oauth2/sign_in?rd=${encodeURIComponent(loc.pathname + loc.search)}`
}

let guardInstalled = false

/**
 * Make react-query treat "signed out" like "offline": queries pause instead
 * of fetching, so refetchInterval and invalidateQueries stop sending
 * requests. Keeps the normal browser online/offline handling too.
 * Safe to call more than once.
 */
export function installSignedOutQueryGuard(): void {
  if (guardInstalled) return
  guardInstalled = true
  onlineManager.setEventListener(setOnline => {
    let browserOnline = true
    const update = () => setOnline(browserOnline && !isSignedOut())
    const onOnline = () => { browserOnline = true; update() }
    const onOffline = () => { browserOnline = false; update() }
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    const unsubscribe = subscribeSignedOut(update)
    update()
    return () => {
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
      unsubscribe()
    }
  })
}
