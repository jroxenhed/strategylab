/**
 * Unit tests for the SSE frame parser — specifically the malformed-event
 * handling path that was added in F175.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { parseSseFrame } from './sseParser'

let warnSpy: ReturnType<typeof vi.spyOn>

afterEach(() => {
  warnSpy?.mockRestore()
})

describe('parseSseFrame', () => {
  it('parses valid SSE event', () => {
    const result = parseSseFrame(['{"type":"started","total":4}'])
    expect(result).toEqual({ type: 'started', total: 4 })
  })

  it('parses multi-line data event', () => {
    const result = parseSseFrame(['{"type":"result",', '"windows":[]}'])
    expect(result).toMatchObject({ type: 'result', windows: [] })
  })

  it('returns null for empty input without calling console.warn', () => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const result = parseSseFrame([])
    expect(result).toBeNull()
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('returns null and warns on malformed JSON', () => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const result = parseSseFrame(['{invalid json'])
    expect(result).toBeNull()
    expect(warnSpy).toHaveBeenCalledOnce()
    expect(warnSpy.mock.calls[0][0]).toContain('Malformed SSE event')
  })

  it('returns null and warns on truncated JSON', () => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const result = parseSseFrame(['{"type":"progress","completed":'])
    expect(result).toBeNull()
    expect(warnSpy).toHaveBeenCalledOnce()
    expect(warnSpy.mock.calls[0][0]).toContain('Malformed SSE event')
  })

  it('returns null for JSON null literal without warning', () => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseSseFrame(['null'])).toBeNull()
    expect(warnSpy).not.toHaveBeenCalled()
    warnSpy.mockRestore()
  })

  it('returns null and warns on whitespace-only line', () => {
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseSseFrame(['   '])).toBeNull()
    expect(warnSpy).toHaveBeenCalled()
    warnSpy.mockRestore()
  })
})
