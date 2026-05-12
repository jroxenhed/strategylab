export function apiErrorDetail(e: unknown, fallback: string): string {
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
    const message = (e as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  return fallback
}
