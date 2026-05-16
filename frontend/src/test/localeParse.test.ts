import { describe, it, expect } from 'vitest'
import { fmtNum, parseNumeric } from '../shared/utils/format'

describe('fmtNum — en-US decimal formatting', () => {
  it('formats 3.0935 to "3.09" (en-US, 2 decimal places)', () => {
    expect(fmtNum(3.0935)).toBe('3.09')
  })

  it('formats a whole number without decimals', () => {
    expect(fmtNum(1000)).toBe('1,000')
  })

  it('respects custom maxFractionDigits', () => {
    expect(fmtNum(3.0935, 3)).toBe('3.094')
  })

  it('never produces a comma decimal separator', () => {
    const result = fmtNum(3.0935)
    // Must use period as decimal separator (en-US)
    expect(result).toMatch(/^\d[\d,]*\.\d+$/)
  })
})

describe('parseNumeric — locale-tolerant parsing', () => {
  it('parses "3,09" (Swedish comma decimal) → 3.09', () => {
    expect(parseNumeric('3,09')).toBeCloseTo(3.09)
  })

  it('parses "  $1,234.56  " → 1234.56 (strips junk + thousand separator)', () => {
    expect(parseNumeric('  $1,234.56  ')).toBeCloseTo(1234.56)
  })

  it('parses plain en-US decimal "2.5" → 2.5', () => {
    expect(parseNumeric('2.5')).toBeCloseTo(2.5)
  })

  it('parses European format "1.234,56" → 1234.56', () => {
    expect(parseNumeric('1.234,56')).toBeCloseTo(1234.56)
  })

  it('parses negative "-3.5" → -3.5', () => {
    expect(parseNumeric('-3.5')).toBeCloseTo(-3.5)
  })

  it('parses integer string "42" → 42', () => {
    expect(parseNumeric('42')).toBe(42)
  })

  it('returns NaN for empty string', () => {
    expect(parseNumeric('')).toBeNaN()
  })
})
