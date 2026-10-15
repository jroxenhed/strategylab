/**
 * toMonacoRange (F435 W7, spec S47, plan W7 contracts): the backend sends
 * Python's positions (line 1-based, col 0-based, in characters); Monaco
 * wants 1-based columns. This is the one conversion.
 */

import { describe, it, expect } from 'vitest'
import { diagnosticText, toMonacoRange } from '../../../api/nodebuilderCode'

describe('toMonacoRange', () => {
  it('turns a 0-based col and end_col into 1-based Monaco columns', () => {
    expect(toMonacoRange({ line: 2, col: 8, end_line: 2, end_col: 12 }))
      .toEqual({ startLineNumber: 2, startColumn: 9, endLineNumber: 2, endColumn: 13 })
  })

  it('with no end, the range is at least one character wide', () => {
    const r = toMonacoRange({ line: 3, col: 4, end_line: null, end_col: null })
    expect(r.startLineNumber).toBe(3)
    expect(r.startColumn).toBe(5)
    expect(r.endLineNumber).toBe(3)
    expect(r.endColumn).toBeGreaterThanOrEqual(r.startColumn + 1)
  })

  it('a SyntaxError at Python offset 5 (col 4) starts at Monaco column 5', () => {
    // The backend sends col = offset - 1; Monaco adds the 1 back.
    expect(toMonacoRange({ line: 1, col: 4, end_line: null, end_col: null }).startColumn).toBe(5)
  })

  it('an end before the start on the same line still gives a one-character range', () => {
    const r = toMonacoRange({ line: 1, col: 6, end_line: 1, end_col: 2 })
    expect(r.endColumn).toBe(r.startColumn + 1)
  })

  it('keeps a multi-line range', () => {
    expect(toMonacoRange({ line: 1, col: 0, end_line: 3, end_col: 5 }))
      .toEqual({ startLineNumber: 1, startColumn: 1, endLineNumber: 3, endColumn: 6 })
  })

  it('a diagnostic with no line marks line 1 from column 1', () => {
    const r = toMonacoRange({ line: null, col: null, end_line: null, end_col: null })
    expect(r.startLineNumber).toBe(1)
    expect(r.startColumn).toBe(1)
  })
})

describe('diagnosticText (S44 status line)', () => {
  it('reads `<code> at <line>:<col>: <message>`', () => {
    expect(diagnosticText({ code: 'code_syntax', line: 1, col: 12, message: 'unexpected token' }))
      .toBe('code_syntax at 1:12: unexpected token')
  })

  it('leaves the position out when there is none', () => {
    expect(diagnosticText({ code: 'code_disabled', line: null, col: null, message: 'off' })).toBe('code_disabled: off')
  })
})
