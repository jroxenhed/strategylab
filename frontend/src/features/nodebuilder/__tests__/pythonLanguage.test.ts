/**
 * The Python language for the code editors (F435 W7, spec S47): the four
 * `@attr` sugar rules, the pure tokenizer the node cards use, the theme
 * colors against tokens.css, the one-time registration, and completion.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  buildNbPythonLanguage,
  CODE_TOKEN_COLORS,
  highlightPython,
  NB_CODE_THEME,
  NB_PREPEND_RULES,
  NB_PYTHON_ID,
  registerNbPython,
  resetNbPythonRegistration,
  type MonacoLanguagesApi,
  type TokenSpan,
} from '../code/pythonLanguage'
import { attrDetail, completionsFor, helperSnippet, signatureArgs, type CompletionAttr } from '../code/completion'

/** The class of the span whose text is `text` (first match). */
function clsOf(spans: TokenSpan[], text: string): string | undefined {
  return spans.find(s => s.text === text)?.cls
}

describe('highlightPython: the sugar rules', () => {
  it('colors the S47 example by class', () => {
    const spans = highlightPython('@spread_z = sl.zscore(spread, chi("lookback", 20))  # note')
    expect(clsOf(spans, '@spread_z')).toBe('attr.write')
    expect(clsOf(spans, 'sl.zscore')).toBe('func')
    expect(clsOf(spans, 'spread')).toBe('identifier')
    expect(clsOf(spans, 'chi')).toBe('ch')
    expect(clsOf(spans, '"lookback"')).toBe('string')
    expect(clsOf(spans, '20')).toBe('number')
    expect(clsOf(spans, '# note')).toBe('comment')
  })

  it('leaves `a @ b` (matmul with spaces) without an attr span', () => {
    const spans = highlightPython('z = a @ b')
    expect(spans.some(s => s.cls === 'attr' || s.cls === 'attr.write')).toBe(false)
  })

  it('tells reads from writes: =, op=, and an annotation are writes; == is a read', () => {
    expect(clsOf(highlightPython('@x += 1'), '@x')).toBe('attr.write')
    expect(clsOf(highlightPython('@flag: bool = @a > @b'), '@flag')).toBe('attr.write')
    expect(clsOf(highlightPython('@flag: bool = @a > @b'), '@a')).toBe('attr')
    expect(clsOf(highlightPython('y = @close == 2'), '@close')).toBe('attr')
  })

  it('keeps strings and comments whole (no attr inside them)', () => {
    const spans = highlightPython('s = "@close"  # uses @close')
    expect(clsOf(spans, '"@close"')).toBe('string')
    expect(clsOf(spans, '# uses @close')).toBe('comment')
    expect(spans.some(s => s.cls === 'attr')).toBe(false)
  })

  it('colors Python keywords and covers every character exactly once', () => {
    const code = 'if @close > 0:\n    @flag = True\nelse:\n    pass'
    const spans = highlightPython(code)
    expect(clsOf(spans, 'if')).toBe('keyword')
    expect(clsOf(spans, 'True')).toBe('keyword')
    const lines = code.split('\n')
    for (let i = 0; i < lines.length; i++) {
      const text = spans.filter(s => s.line === i + 1).map(s => s.text).join('')
      expect(text).toBe(lines[i])
    }
  })

  it('carries a triple-quoted string over lines', () => {
    const spans = highlightPython('x = """a\n@close b"""\n@y = 1')
    expect(spans.filter(s => s.line === 2).every(s => s.cls === 'string')).toBe(true)
    expect(clsOf(spans, '@y')).toBe('attr.write')
  })
})

describe('the Monaco language definition', () => {
  it('puts the four rules in front of the built-in root state, without changing the base', () => {
    const base = { tokenizer: { root: [[/x/, 'identifier']], other: [] }, tokenPostfix: '.python' }
    const lang = buildNbPythonLanguage(base)
    const root = lang.tokenizer.root as [RegExp, string][]
    expect(root.slice(0, 4).map(r => r[1])).toEqual(['attr.write', 'attr', 'ch', 'func'])
    expect(root[4]).toEqual([/x/, 'identifier'])
    expect(base.tokenizer.root).toHaveLength(1)
    expect(lang.tokenPostfix).toBe('.python')
  })

  it('writes "@" as \\x40 in the rule regexes (Monarch reads @word as an attribute)', () => {
    for (const [re] of NB_PREPEND_RULES) expect(re.source.includes('@')).toBe(false)
  })
})

// Vitest leaves CSS imports empty, so the file is read from disk. The module
// name is built at run time so the app's type check (no Node types) skips it.
const nodeFs = await import(/* @vite-ignore */ ['node', 'fs'].join(':')) as { readFileSync(p: string, enc: string): string }
const cwd = (globalThis as unknown as { process: { cwd(): string } }).process.cwd()
const css = nodeFs.readFileSync(`${cwd}/src/features/nodebuilder/tokens.css`, 'utf8')

describe('theme colors match tokens.css (FA5)', () => {
  it.each(Object.entries(CODE_TOKEN_COLORS))('%s', (name, value) => {
    const m = new RegExp(`${name}:\\s*([^;]+);`).exec(css)
    expect(m, `${name} is in tokens.css`).not.toBeNull()
    expect(m![1].trim().toLowerCase()).toBe(value.toLowerCase())
  })

  it('the theme uses those values', () => {
    const rule = (t: string) => NB_CODE_THEME.rules.find(r => r.token === t)
    expect(rule('attr')?.foreground).toBe('c084fc')
    expect(rule('attr.write')?.fontStyle).toBe('bold')
    expect(rule('func')?.foreground).toBe('34d399')
    expect(rule('keyword')?.foreground).toBe('c3c9d6')
    expect(rule('comment')?.fontStyle).toBe('italic')
    expect(NB_CODE_THEME.colors['editor.background']).toBe(CODE_TOKEN_COLORS['--nb-bg-input'])
  })
})

describe('registerNbPython', () => {
  beforeEach(() => resetNbPythonRegistration())

  function fakeMonaco(): MonacoLanguagesApi {
    return {
      languages: {
        register: vi.fn(),
        setMonarchTokensProvider: vi.fn(),
        setLanguageConfiguration: vi.fn(),
        registerCompletionItemProvider: vi.fn(),
        registerHoverProvider: vi.fn(),
      },
      editor: { defineTheme: vi.fn() },
    }
  }

  it('registers the language, theme and providers once, however often it is called', () => {
    const m = fakeMonaco()
    const base = { language: { tokenizer: { root: [] } }, conf: { comments: { lineComment: '#' } } }
    expect(registerNbPython(m, base, { completion: {}, hover: {} })).toBe(true)
    expect(registerNbPython(m, base, { completion: {}, hover: {} })).toBe(false)
    expect(m.languages.register).toHaveBeenCalledTimes(1)
    expect(m.languages.register).toHaveBeenCalledWith({ id: NB_PYTHON_ID })
    expect(m.languages.registerCompletionItemProvider).toHaveBeenCalledTimes(1)
    expect(m.languages.registerHoverProvider).toHaveBeenCalledTimes(1)
    expect(m.editor.defineTheme).toHaveBeenCalledTimes(1)
    expect(m.languages.setLanguageConfiguration).toHaveBeenCalledWith(NB_PYTHON_ID, base.conf)
  })

  it('falls back to a small python definition when the built-in one is missing', () => {
    const m = fakeMonaco()
    registerNbPython(m, null)
    const def = (m.languages.setMonarchTokensProvider as ReturnType<typeof vi.fn>).mock.calls[0][1]
    expect(def.tokenizer.root.length).toBeGreaterThan(4)
  })
})

describe('completion (S47)', () => {
  const attrs: CompletionAttr[] = [
    { name: '@close', dtype: 'float', cls: 'point', from: 'aapl' },
    { name: '@spy_close', dtype: 'float', cls: 'point', from: 'spy' },
    { name: '@stop_pct', dtype: 'float', cls: 'detail', from: 'sl' },
  ]
  const functions = [{ name: 'sl.rsi', signature: 'sl.rsi(x, period=14)', returns: 'series_float', doc: 'RSI, as the RSI node.' }]

  it('after @ it offers the stream attributes with their detail strings', () => {
    const items = completionsFor({ textBefore: 'x = @s', code: 'x = @s', attrs, detailOnly: false, functions, paths: [] })
    const close = items.find(i => i.label === '@close')
    const spy = items.find(i => i.label === '@spy_close')
    expect(spy?.detail).toBe('float · point · from spy')
    expect(close?.detail).toBe('float · point · from aapl')
    // Monaco filters by the typed prefix; both are provided, replacing `@s`.
    expect(spy?.replace).toBe(2)
  })

  it('an expression offers detail attributes only', () => {
    const items = completionsFor({ textBefore: '@', code: '@', attrs, detailOnly: true, functions, paths: [] })
    expect(items.map(i => i.label)).toEqual(['@stop_pct'])
  })

  it('puts attributes the code already reads first', () => {
    const code = 'y = @spy_close\nz = @'
    const items = completionsFor({ textBefore: 'z = @', code, attrs, detailOnly: false, functions, paths: [] })
    expect(items[0].label).toBe('@spy_close')
  })

  it('after sl. it offers the helpers as snippets built from the signature', () => {
    const items = completionsFor({ textBefore: 'a = sl.', code: '', attrs, detailOnly: false, functions, paths: [] })
    expect(items).toHaveLength(1)
    expect(items[0].label).toBe('sl.rsi')
    expect(items[0].detail).toBe('sl.rsi(x, period=14) → series_float')
    expect(items[0].insertText).toBe('rsi(${1:x}, ${2:period})')
    expect(items[0].snippet).toBe(true)
  })

  it('inside ch(" it offers graph paths', () => {
    const items = completionsFor({ textBefore: 'th = chf("../', code: '', attrs, detailOnly: false, functions, paths: ['../rsi/', '../rsi/period', '/'] })
    expect(items.map(i => i.label)).toEqual(['../rsi/', '../rsi/period', '/'])
    expect(items[0].replace).toBe(3)
  })

  it('otherwise it offers np, pd, sl and the ch family', () => {
    const labels = completionsFor({ textBefore: 'x = ', code: '', attrs, detailOnly: false, functions, paths: [] }).map(i => i.label)
    expect(labels).toEqual(expect.arrayContaining(['np', 'pd', 'sl', 'chf', 'chi', 'chb', 'chs', 'chv', 'ch']))
  })

  it('reads signature arguments, defaults with brackets included', () => {
    expect(signatureArgs('sl.rolling(x, window, op="mean")')).toEqual(['x', 'window', 'op'])
    expect(signatureArgs('sl.f(a, b=(1, 2), *args)')).toEqual(['a', 'b', 'args'])
    expect(helperSnippet({ name: 'sl.bars_since', signature: 'sl.bars_since(cond)' })).toBe('bars_since(${1:cond})')
    expect(attrDetail({ name: '@x', dtype: 'bool', cls: 'detail', from: null })).toBe('bool · detail')
  })
})
