/**
 * Relative time tests (F435 Wave 1 item 1.S, surfaces-w1-w4.md 0.6): every
 * boundary of just now / s / min / h / yesterday / date. Times are built in
 * local time so the test does not depend on the machine's time zone.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import {
  formatRelativeTime, formatFullTimestamp, useRelativeTime, RELATIVE_TIME_REFRESH_MS,
} from '../ui/relativeTime'

const now = new Date(2026, 8, 30, 14, 0, 0) // 2026-09-30 14:00:00 local
const ago = (ms: number) => new Date(now.getTime() - ms)
const S = 1000
const MIN = 60 * S
const H = 60 * MIN

afterEach(() => vi.useRealTimers())

describe('formatRelativeTime', () => {
  it.each([
    [0, 'just now'],
    [9_999, 'just now'],
    [10 * S, '10 s ago'],
    [59 * S + 999, '59 s ago'],
    [60 * S, '1 min ago'],
    [59 * MIN + 59 * S, '59 min ago'],
    [60 * MIN, '1 h ago'],
    [23 * H + 59 * MIN, '23 h ago'],
  ])('%i ms ago reads %s', (ms, text) => {
    expect(formatRelativeTime(ago(ms), now)).toBe(text)
  })

  it('24 h back on the previous calendar day reads yesterday', () => {
    expect(formatRelativeTime(ago(24 * H), now)).toBe('yesterday')
    expect(formatRelativeTime(new Date(2026, 8, 29, 0, 0, 1), now)).toBe('yesterday')
  })

  it('two calendar days back reads the date', () => {
    expect(formatRelativeTime(new Date(2026, 8, 28, 23, 59, 0), now)).toBe('2026-09-28')
    expect(formatRelativeTime(new Date(2025, 0, 5, 9, 0, 0), now)).toBe('2025-01-05')
  })

  it('a time slightly in the future reads just now; bad input reads empty', () => {
    expect(formatRelativeTime(new Date(now.getTime() + 5 * S), now)).toBe('just now')
    expect(formatRelativeTime('not a date', now)).toBe('')
  })

  it('accepts ISO strings and numbers', () => {
    expect(formatRelativeTime(ago(5 * MIN).toISOString(), now)).toBe('5 min ago')
    expect(formatRelativeTime(ago(5 * MIN).getTime(), now.getTime())).toBe('5 min ago')
  })

  it('full timestamp for titles', () => {
    expect(formatFullTimestamp(new Date(2026, 8, 30, 9, 5, 7))).toBe('2026-09-30 09:05:07')
  })
})

describe('useRelativeTime', () => {
  it('refreshes every 30 s', () => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
    const { result } = renderHook(() => useRelativeTime(now))
    expect(result.current).toBe('just now')
    act(() => { vi.advanceTimersByTime(RELATIVE_TIME_REFRESH_MS) })
    expect(result.current).toBe('30 s ago')
    act(() => { vi.advanceTimersByTime(RELATIVE_TIME_REFRESH_MS) })
    expect(result.current).toBe('1 min ago')
  })
})
