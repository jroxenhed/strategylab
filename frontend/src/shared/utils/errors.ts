export function apiErrorDetail(e: unknown, fallback: string): string {
  if (e && typeof e === 'object') {
    const resp = (e as { response?: { data?: { detail?: unknown } } }).response
    const detail = resp?.data?.detail
    if (typeof detail === 'string' && detail) return detail
    const message = (e as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  return fallback
}
