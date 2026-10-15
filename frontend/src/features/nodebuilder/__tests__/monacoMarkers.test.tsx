/**
 * parse_code diagnostics reach Monaco at the right range (F435 W7, S47).
 *
 * jsdom never loads Monaco, so the loader is mocked with a small fake that
 * records setModelMarkers. The diagnostics are ones the live
 * /api/nodebuilder/parse_code route returned for this code (line 1-based,
 * col 0-based, in characters of the user's text, @attr sugar included);
 * each marker must cover exactly the text the server pointed at.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act, cleanup } from '@testing-library/react'
import type { Diagnostic } from '../../../api/nodebuilderValidate'

interface FakeMarker {
  startLineNumber: number
  startColumn: number
  endLineNumber: number
  endColumn: number
  severity: number
  message: string
}

const calls: FakeMarker[][] = []
let modelText = ''

function fakeMonaco() {
  const lines = () => modelText.split('\n')
  const model = {
    uri: { toString: () => 'inmemory://fake/1' },
    getValue: () => modelText,
    setValue: (v: string) => { modelText = v },
    getLineMaxColumn: (n: number) => (lines()[n - 1]?.length ?? 0) + 1,
    getPositionAt: (o: number) => ({ lineNumber: 1, column: o + 1 }),
    onDidChangeContent: () => ({ dispose() {} }),
    dispose() {},
  }
  const editor = {
    getModel: () => model,
    createDecorationsCollection: () => ({ set() {} }),
    setPosition() {},
    setSelection() {},
    focus() {},
    hasTextFocus: () => false,
    onDidBlurEditorText: () => ({ dispose() {} }),
    addCommand() {},
    createContextKey: () => ({ set() {}, reset() {}, get: () => true }),
    addAction: () => ({ dispose() {} }),
    updateOptions() {},
    layout() {},
    dispose() {},
  }
  return {
    editor: {
      createModel: (text: string) => { modelText = text; return model },
      create: () => editor,
      setModelMarkers: (_m: unknown, _owner: string, list: FakeMarker[]) => { calls.push(list) },
    },
    MarkerSeverity: { Error: 8, Warning: 4, Info: 2 },
    KeyMod: { CtrlCmd: 2048, Shift: 1024 },
    KeyCode: { Enter: 3, Escape: 9, Tab: 2 },
    Range: { fromPositions: () => ({}) },
  }
}

vi.mock('../code/monacoLoader', () => ({
  loadMonaco: () => Promise.resolve(fakeMonaco()),
}))

const { default: MonacoEditor } = await import('../code/MonacoEditor')

/** The text a Monaco range covers in `text` (1-based columns, end exclusive). */
function covered(text: string, m: FakeMarker): string {
  const lines = text.split('\n')
  if (m.startLineNumber !== m.endLineNumber) {
    return [lines[m.startLineNumber - 1].slice(m.startColumn - 1), ...lines.slice(m.startLineNumber, m.endLineNumber - 1), lines[m.endLineNumber - 1].slice(0, m.endColumn - 1)].join('\n')
  }
  return lines[m.startLineNumber - 1].slice(m.startColumn - 1, m.endColumn - 1)
}

function diag(d: Partial<Diagnostic>): Diagnostic {
  return { code: 'code_syntax', severity: 'error', message: 'm', node_id: 'w', ...d } as Diagnostic
}

async function mount(value: string, markers: Diagnostic[], markersSource?: string) {
  const r = render(<MonacoEditor ariaLabel="Code" value={value} markers={markers} markersSource={markersSource} testId="ed" />)
  await act(async () => { await Promise.resolve() })
  return r
}

beforeEach(() => {
  calls.length = 0
  cleanup()
})

describe('parse_code diagnostics as Monaco markers', () => {
  it('the editor is the loaded Monaco, not the stand-in', async () => {
    const { getByTestId } = await mount('x = 1', [])
    expect(getByTestId('ed').getAttribute('data-editor-state')).toBe('loaded')
  })

  it('a span after @attr sugar covers exactly the text the server named', async () => {
    const code = '@out = @close[0] * @ volume'
    await mount(code, [diag({ line: 1, col: 19, end_line: 1, end_col: 27, message: 'Write @volume with no space after the @.' })])
    const m = calls.at(-1)![0]
    expect(covered(code, m)).toBe('@ volume')
    expect(m.severity).toBe(8)
    expect(m.message).toBe('Write @volume with no space after the @. (code_syntax)')
  })

  it('columns count characters, so text with é before the call lines up', async () => {
    const code = "s = 'éé'; x = ch(s)"
    await mount(code, [diag({ code: 'ch_dynamic', line: 1, col: 14, end_line: 1, end_col: 19 })])
    expect(covered(code, calls.at(-1)![0])).toBe('ch(s)')
  })

  it('a ref_broken path on line 2 of a block, as a warning', async () => {
    const code = "x = 1\ny = chf('../nope/x')"
    await mount(code, [diag({ code: 'ref_broken', severity: 'warning', line: 2, col: 4, end_line: 2, end_col: 20 })])
    const m = calls.at(-1)![0]
    expect(covered(code, m)).toBe("chf('../nope/x')")
    expect(m.severity).toBe(4)
  })

  it('a point error (end_col == col) marks one character', async () => {
    const code = '@out = @close + )'
    await mount(code, [diag({ line: 1, col: 16, end_line: 1, end_col: 16, message: "unmatched ')'" })])
    expect(covered(code, calls.at(-1)![0])).toBe(')')
  })

  it('a diagnostic with no line marks the whole first line', async () => {
    const code = 'import os\nx = 1'
    await mount(code, [diag({ code: 'code_disabled' })])
    expect(covered(code, calls.at(-1)![0])).toBe('import os')
  })

  it('an answer for older text is not applied at stale positions', async () => {
    const r = await mount('@a = @close +', [], '@a = @close +')
    const before = calls.length
    // The user typed on; the answer is for the text the parse was sent with.
    r.rerender(<MonacoEditor ariaLabel="Code" value="@a = @close + 1" markers={[diag({ line: 1, col: 13 })]} markersSource="@a = @close" testId="ed" />)
    await act(async () => { await Promise.resolve() })
    expect(calls.length).toBe(before)
    // The answer for the current text is applied.
    r.rerender(<MonacoEditor ariaLabel="Code" value="@a = @close +" markers={[diag({ line: 1, col: 13 })]} markersSource="@a = @close +" testId="ed" />)
    await act(async () => { await Promise.resolve() })
    expect(calls.length).toBe(before + 1)
  })
})
