import { SignedOutError, isSignInUrl, isSignedOut, markSignedOut } from '../shared/auth/signedOut'

/**
 * fetch() for our own API, with sign-in handling. Use it instead of a bare
 * fetch() for any /api/ call.
 *
 * - Asks for JSON (Accept: application/json) unless the caller set Accept.
 *   oauth2-proxy then answers an expired sign-in with 401 instead of a
 *   redirect to Google, which the browser could only report as a network
 *   error.
 * - On 401, or a redirect that ended on the sign-in page, it marks the app as
 *   signed out and throws SignedOutError. Callers already treat a throw as
 *   "could not reach the server", so their fallbacks keep working.
 * - Once signed out it throws SignedOutError at once and sends nothing.
 *
 * Any other response (including 4xx/5xx from the backend) comes back as is.
 */
export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  if (isSignedOut()) throw new SignedOutError()
  const resp = await fetch(url, { ...init, headers: withJsonAccept(init.headers) })
  if (
    resp.status === 401 ||
    resp.type === 'opaqueredirect' ||
    (resp.redirected && isSignInUrl(resp.url))
  ) {
    markSignedOut()
    throw new SignedOutError()
  }
  return resp
}

function withJsonAccept(headers: HeadersInit | undefined): HeadersInit {
  if (headers instanceof Headers) {
    const copy = new Headers(headers)
    if (!copy.has('Accept')) copy.set('Accept', 'application/json')
    return copy
  }
  if (Array.isArray(headers)) {
    const hasAccept = headers.some(([k]) => k.toLowerCase() === 'accept')
    return hasAccept ? headers : [...headers, ['Accept', 'application/json']]
  }
  const obj = { ...(headers ?? {}) } as Record<string, string>
  const hasAccept = Object.keys(obj).some(k => k.toLowerCase() === 'accept')
  return hasAccept ? obj : { Accept: 'application/json', ...obj }
}
