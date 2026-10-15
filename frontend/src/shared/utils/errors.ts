import { SIGNED_OUT_TEXT, isAuthError } from '../auth/signedOut'

export function apiErrorDetail(e: unknown, fallback: string): string {
  // A sign-in failure is not a server error: always show the plain text.
  if (isAuthError(e)) return SIGNED_OUT_TEXT
  if (e && typeof e === 'object') {
    const resp = (e as { response?: { data?: { detail?: unknown } } }).response
    const detail = resp?.data?.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail) && detail.length > 0) {
      return detail
        .map((d) => {
          if (d && typeof d === 'object') {
            const locArr = (d as { loc?: unknown[] }).loc
            const loc = Array.isArray(locArr)
              ? locArr.filter((s) => s !== 'body').join('.')
              : ''
            const msg = (d as { msg?: unknown }).msg
            if (typeof msg === 'string') return loc ? `${loc}: ${msg}` : msg
          }
          return String(d)
        })
        .join('; ')
    }
    if (detail !== null && typeof detail === 'object' && !Array.isArray(detail)) {
      const reason = (detail as { reason?: unknown }).reason
      if (typeof reason === 'string' && reason) return reason
      const error = (detail as { error?: unknown }).error
      if (typeof error === 'string' && error) return error
      const detailMessage = (detail as { message?: unknown }).message
      if (typeof detailMessage === 'string' && detailMessage) return detailMessage
    }
    const message = (e as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  return fallback
}
