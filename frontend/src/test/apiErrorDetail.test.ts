import { describe, it, expect } from 'vitest'
import { apiErrorDetail } from '../shared/utils/errors'

function axiosErr(detail: unknown, message = 'Request failed with status code 400') {
  return { message, response: { data: { detail } } }
}

describe('apiErrorDetail', () => {
  it('returns a string detail', () => {
    expect(apiErrorDetail(axiosErr('bad ticker'), 'fb')).toBe('bad ticker')
  })

  it('returns detail.message for a coded object detail', () => {
    const e = axiosErr({ code: 'leg_invalid', message: 'Leg long_leg has capital weight 0.' })
    expect(apiErrorDetail(e, 'fb')).toBe('Leg long_leg has capital weight 0.')
  })

  it('prefers reason and error over message', () => {
    expect(apiErrorDetail(axiosErr({ reason: 'r', message: 'm' }), 'fb')).toBe('r')
    expect(apiErrorDetail(axiosErr({ error: 'e', message: 'm' }), 'fb')).toBe('e')
  })

  it('falls back to the axios message when the object detail has no text', () => {
    expect(apiErrorDetail(axiosErr({ code: 'x', message: 3 }), 'fb')).toBe(
      'Request failed with status code 400',
    )
  })

  it('returns the fallback for a non-object error', () => {
    expect(apiErrorDetail(null, 'fb')).toBe('fb')
  })
})
