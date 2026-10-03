/**
 * Python with the `@attr` sugar, for the code editors (F435 W7, spec S47).
 *
 * Code nodes are real Python. The editor uses Monaco's built-in `python`
 * language and puts four rules in front of its tokenizer:
 *   1. `attr.write`  `@name` followed by `=` (not `==`), an `op=`, or `:`
 *   2. `attr`        any other `@name` (lowercase, as the server's sugar)
 *   3. `ch`          `ch chf chi chb chs chv` right before `(`
 *   4. `func`        `sl.name` right before `(`, as one token
 * Colors are cosmetic only: the server's sugar rule decides what code means.
 *
 * The node cards never load Monaco (spec S45 must-not). They color their
 * read-only code with `highlightPython()`, a small pure tokenizer that uses
 * the same four regexes and a short Python fallback for the rest.
 *
 * This file has no Monaco import. `registerNbPython(monaco, base)` takes the
 * Monaco namespace and the built-in python definition from the lazy loader,
 * so tests can pass fakes and the main bundle never pulls Monaco in.
 */

// The regexes use \x40 for "@": Monarch reads "@word" inside a regex as a
// reference to a language attribute.
const NAME = '[a-z_][a-z0-9_]*'
/** What follows a written attribute: `=` (not `==`), an augmented `op=`, or `:` (annotation). */
const WRITE_TAIL = '(?=\\s*(?:=(?!=)|\\*\\*=|\\/\\/=|>>=|<<=|[-+*\\/%&|^\\x40]=|:(?!=)))'

export const ATTR_WRITE_RE = new RegExp(`\\x40${NAME}${WRITE_TAIL}`)
export const ATTR_RE = new RegExp(`\\x40${NAME}`)
export const CH_RE = /\b(?:ch|chf|chi|chb|chs|chv)\b(?=\()/
export const FUNC_RE = /\bsl\.[a-z_][a-z0-9_]*(?=\()/

/** The rules put in front of the built-in python tokenizer's `root` state, in priority order. */
export const NB_PREPEND_RULES: readonly [RegExp, string][] = [
  [ATTR_WRITE_RE, 'attr.write'],
  [ATTR_RE, 'attr'],
  [CH_RE, 'ch'],
  [FUNC_RE, 'func'],
]

/** The language id the code models use. */
export const NB_PYTHON_ID = 'nb-python'
/** The editor theme. */
export const NB_THEME_ID = 'nb-code-dark'

// ---------------------------------------------------------------------------
// Colors (FA5). Monaco needs literal hex; a test compares these with
// tokens.css by name.
// ---------------------------------------------------------------------------

export const CODE_TOKEN_COLORS = {
  '--nb-code-comment': '#6b7386',
  '--nb-code-string': '#fcd34d',
  '--nb-code-keyword': '#c084fc',
  '--nb-code-func': '#34d399',
  '--nb-code-pykw': '#c3c9d6',
  '--nb-code-number': '#7dd3fc',
  '--nb-code-local': '#eef1f6',
  '--nb-code-expr': '#fcd34d',
  '--nb-bg-input': '#0a0c10',
  '--nb-bg-elevated': '#161b25',
  '--nb-text-dim': '#7a8296',
  '--nb-text-secondary': '#c3c9d6',
  '--nb-text-muted': '#98a1b3',
  '--nb-error': '#f87171',
  '--nb-warn': '#fbbf24',
} as const

const C = CODE_TOKEN_COLORS
const hex = (v: string) => v.replace('#', '')

/** One Monaco theme rule. */
export interface ThemeRule { token: string; foreground?: string; fontStyle?: string }

/** The `nb-code-dark` theme (S47 table). */
export const NB_CODE_THEME = {
  base: 'vs-dark' as const,
  inherit: false,
  rules: [
    { token: '', foreground: hex(C['--nb-code-local']) },
    { token: 'comment', foreground: hex(C['--nb-code-comment']), fontStyle: 'italic' },
    { token: 'string', foreground: hex(C['--nb-code-string']) },
    { token: 'attr', foreground: hex(C['--nb-code-keyword']) },
    { token: 'attr.write', foreground: hex(C['--nb-code-keyword']), fontStyle: 'bold' },
    { token: 'ch', foreground: hex(C['--nb-code-keyword']), fontStyle: 'bold' },
    { token: 'func', foreground: hex(C['--nb-code-func']) },
    { token: 'keyword', foreground: hex(C['--nb-code-pykw']), fontStyle: 'bold' },
    { token: 'tag', foreground: hex(C['--nb-code-pykw']), fontStyle: 'bold' },
    { token: 'number', foreground: hex(C['--nb-code-number']) },
    { token: 'delimiter', foreground: hex(C['--nb-code-local']) },
    { token: 'identifier', foreground: hex(C['--nb-code-local']) },
  ] as ThemeRule[],
  colors: {
    'editor.background': C['--nb-bg-input'],
    'editor.foreground': C['--nb-code-local'],
    'editorLineNumber.foreground': C['--nb-text-dim'],
    'editorLineNumber.activeForeground': C['--nb-text-secondary'],
    'editor.lineHighlightBackground': '#ffffff08',
    'editor.selectionBackground': '#38bdf840',
    'editorCursor.foreground': C['--nb-code-local'],
    'editorGutter.background': C['--nb-bg-input'],
    'editorWidget.background': C['--nb-bg-elevated'],
    'editorWidget.border': '#2f3848',
    'editorSuggestWidget.selectedBackground': '#ffffff14',
    'editorError.foreground': C['--nb-error'],
    'editorWarning.foreground': C['--nb-warn'],
    'editorInfo.foreground': C['--nb-text-muted'],
    'scrollbarSlider.background': '#ffffff14',
  } as Record<string, string>,
}

// ---------------------------------------------------------------------------
// The language: the built-in python definition with the rules prepended
// ---------------------------------------------------------------------------

/** The parts of a Monarch definition this file touches. */
export interface MonarchLike {
  tokenizer: Record<string, unknown[]>
  [key: string]: unknown
}

/**
 * A copy of the built-in python Monarch definition with the four rules in
 * front of `tokenizer.root`. The base is never changed.
 */
export function buildNbPythonLanguage(base: MonarchLike): MonarchLike {
  const root = Array.isArray(base.tokenizer?.root) ? base.tokenizer.root : []
  return {
    ...base,
    tokenizer: {
      ...base.tokenizer,
      root: [...NB_PREPEND_RULES.map(([re, token]) => [re, token]), ...root],
    },
  }
}

/** A small python definition, used only when the built-in one cannot load. */
export const FALLBACK_PYTHON: MonarchLike = {
  defaultToken: '',
  tokenPostfix: '.python',
  keywords: [
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
    'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import',
    'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
    'with', 'yield',
  ],
  tokenizer: {
    root: [
      [/#.*$/, 'comment'],
      [/[rRbBuUfF]{0,2}"""/, { token: 'string', next: '@tdq' }],
      [/[rRbBuUfF]{0,2}'''/, { token: 'string', next: '@tsq' }],
      [/[rRbBuUfF]{0,2}"([^"\\]|\\.)*"/, 'string'],
      [/[rRbBuUfF]{0,2}'([^'\\]|\\.)*'/, 'string'],
      [/\d+(\.\d*)?([eE][+-]?\d+)?/, 'number'],
      [/\x40[a-zA-Z_][\w.]*/, 'tag'],
      [/[a-zA-Z_]\w*/, { cases: { '@keywords': 'keyword', '@default': 'identifier' } }],
      [/[{}()[\],:;.]/, 'delimiter'],
    ],
    tdq: [[/"""/, { token: 'string', next: '@pop' }], [/[^"]+/, 'string'], [/"/, 'string']],
    tsq: [[/'''/, { token: 'string', next: '@pop' }], [/[^']+/, 'string'], [/'/, 'string']],
  },
}

/** The Monaco calls this file uses (a fake in tests). */
export interface MonacoLanguagesApi {
  languages: {
    register(def: { id: string }): void
    setMonarchTokensProvider(id: string, def: unknown): unknown
    setLanguageConfiguration(id: string, conf: unknown): unknown
    registerCompletionItemProvider(id: string, provider: unknown): unknown
    registerHoverProvider(id: string, provider: unknown): unknown
  }
  editor: {
    defineTheme(id: string, theme: unknown): void
  }
}

let registered = false

/**
 * Register the `nb-python` language and the `nb-code-dark` theme, once.
 * Calling it again does nothing (S47: never per editor; providers would
 * duplicate). `providers` adds the completion and hover providers.
 */
export function registerNbPython(
  monaco: MonacoLanguagesApi,
  base: { language: MonarchLike; conf?: unknown } | null,
  providers?: { completion?: unknown; hover?: unknown },
): boolean {
  if (registered) return false
  registered = true
  const language = base?.language ?? FALLBACK_PYTHON
  monaco.languages.register({ id: NB_PYTHON_ID })
  monaco.languages.setMonarchTokensProvider(NB_PYTHON_ID, buildNbPythonLanguage(language))
  if (base?.conf) monaco.languages.setLanguageConfiguration(NB_PYTHON_ID, base.conf)
  monaco.editor.defineTheme(NB_THEME_ID, NB_CODE_THEME)
  if (providers?.completion) monaco.languages.registerCompletionItemProvider(NB_PYTHON_ID, providers.completion)
  if (providers?.hover) monaco.languages.registerHoverProvider(NB_PYTHON_ID, providers.hover)
  return true
}

/** Tests only: allow `registerNbPython` to run again. */
export function resetNbPythonRegistration(): void {
  registered = false
}

// ---------------------------------------------------------------------------
// highlightPython: the pure tokenizer for the node cards
// ---------------------------------------------------------------------------

export type TokenClass =
  | 'attr.write' | 'attr' | 'ch' | 'func' | 'keyword' | 'tag' | 'string' | 'number'
  | 'comment' | 'identifier' | 'delimiter' | 'white'

export interface TokenSpan {
  /** 1-based line. */
  line: number
  /** 0-based start column in that line. */
  start: number
  text: string
  cls: TokenClass
}

export const PY_KEYWORDS: ReadonlySet<string> = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
  'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import',
  'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
  'with', 'yield',
])

const STRING_START = /^[rRbBuUfF]{0,2}("""|'''|"|')/
const NUMBER_RE = /^(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?[jJ]?)/
const IDENT_RE = /^[A-Za-z_]\w*/
const TAG_RE = /^\x40[A-Za-z_][\w.]*/
const WHITE_RE = /^\s+/

/** Match `re` exactly at `pos` of `line` (the regexes above use \b and lookahead, so test on the slice with context). */
function matchAt(re: RegExp, line: string, pos: number): string | null {
  const sticky = new RegExp(re.source, 'y')
  sticky.lastIndex = pos
  const m = sticky.exec(line)
  return m ? m[0] : null
}

/**
 * Color spans for a piece of Python, with the same four sugar rules as the
 * Monaco language. Every character of the code is in exactly one span.
 */
export function highlightPython(code: string): TokenSpan[] {
  const out: TokenSpan[] = []
  const lines = code.split(/\r?\n/)
  // An open triple-quoted string carries over to the next lines.
  let openTriple: string | null = null
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li]
    const lineNo = li + 1
    let pos = 0
    const push = (text: string, cls: TokenClass) => {
      if (!text) return
      out.push({ line: lineNo, start: pos, text, cls })
      pos += text.length
    }
    if (openTriple) {
      const end = line.indexOf(openTriple)
      if (end < 0) { push(line, 'string'); continue }
      push(line.slice(0, end + 3), 'string')
      openTriple = null
    }
    while (pos < line.length) {
      const rest = line.slice(pos)
      const white = WHITE_RE.exec(rest)
      if (white) { push(white[0], 'white'); continue }
      if (rest[0] === '#') { push(rest, 'comment'); continue }
      const str = STRING_START.exec(rest)
      // A prefix (f, rb, ...) only counts when the quote follows at once.
      if (str) {
        const quote = str[1]
        let i = str[0].length
        if (quote.length === 3) {
          const end = rest.indexOf(quote, i)
          if (end < 0) { push(rest, 'string'); openTriple = quote; break }
          push(rest.slice(0, end + 3), 'string')
          continue
        }
        while (i < rest.length && rest[i] !== quote) i += rest[i] === '\\' ? 2 : 1
        push(rest.slice(0, Math.min(i + 1, rest.length)), 'string')
        continue
      }
      let hit: string | null = null
      for (const [re, cls] of NB_PREPEND_RULES) {
        hit = matchAt(re, line, pos)
        if (hit) { push(hit, cls as TokenClass); break }
      }
      if (hit) continue
      const tag = TAG_RE.exec(rest)
      if (tag) { push(tag[0], 'tag'); continue }
      const num = NUMBER_RE.exec(rest)
      if (num && num[0] && /[\d.]/.test(rest[0]) && !(rest[0] === '.' && !/\d/.test(rest[1] ?? ''))) {
        push(num[0], 'number'); continue
      }
      const ident = IDENT_RE.exec(rest)
      if (ident) { push(ident[0], PY_KEYWORDS.has(ident[0]) ? 'keyword' : 'identifier'); continue }
      push(rest[0], 'delimiter')
    }
  }
  return out
}

/** The spans of one line (1-based), for the node card's per-line render. */
export function spansByLine(spans: readonly TokenSpan[]): Map<number, TokenSpan[]> {
  const m = new Map<number, TokenSpan[]>()
  for (const s of spans) {
    const list = m.get(s.line)
    if (list) list.push(s)
    else m.set(s.line, [s])
  }
  return m
}

/** CSS color per class, for the static node block (S45, S46). */
export const TOKEN_CSS: Record<TokenClass, { color?: string; fontWeight?: number; fontStyle?: string }> = {
  'attr.write': { color: 'var(--nb-code-keyword)', fontWeight: 600 },
  attr: { color: 'var(--nb-code-keyword)' },
  ch: { color: 'var(--nb-code-keyword)', fontWeight: 600 },
  func: { color: 'var(--nb-code-func)' },
  keyword: { color: 'var(--nb-code-pykw)', fontWeight: 600 },
  tag: { color: 'var(--nb-code-pykw)', fontWeight: 600 },
  string: { color: 'var(--nb-code-string)' },
  number: { color: 'var(--nb-code-number)' },
  comment: { color: 'var(--nb-code-comment)', fontStyle: 'italic' },
  identifier: { color: 'var(--nb-code-local)' },
  delimiter: { color: 'var(--nb-code-local)' },
  white: {},
}
