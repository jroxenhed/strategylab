/**
 * Rewrite the path inside each literal `ch*()` call (F435 W7).
 *
 * Client twin of backend `migrate.rewrite_ch_paths`. The backend reads the
 * code with Python 3.12's tokenizer; this file is a small lexer that gives
 * the same tokens for what matters here: names, the `(`, `=`, `.` ops,
 * string literals (prefixes, quote styles, escapes, f-string interiors),
 * comments, and the two places where Python's tokenizer gives up
 * (an unterminated string, a dedent to a level that was never used).
 * As in the backend, edits found before such a problem are kept and the
 * rest of the code is left alone.
 *
 * The rename rule itself (which path becomes what) is `fix`, built by
 * `paths.rewritePathRefs`. Pinned to the backend by the rename vectors in
 * backend/tests/nodebuilder/vectors/ch_refs.json and by
 * __tests__/chRewrite.test.ts (outputs taken from the backend).
 */

const CH_FUNCS = new Set(['ch', 'chf', 'chi', 'chs', 'chb', 'chv'])
/** Characters a rewritten path may not hold (the backend's `_UNSAFE_IN_PATH`). */
const UNSAFE_IN_PATH = /['"\\\n\r]/
const STRING_PREFIXES = new Set(['r', 'u', 'b', 'f', 'br', 'rb', 'fr', 'rf'])
// Longest first, so `==` is never read as `=` and `...` never as `.`.
const OPS = ['**=', '//=', '>>=', '<<=', '...', '!=', '%=', '&=', '**', '*=', '+=', '-=', '->', '//', '/=', ':=', '<<', '<=', '==', '>=', '>>', '@=', '^=', '|=']
const NUMBER_RE = /0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?[jJ]?/y
const NAME_RE = /[A-Za-z_À-￿][\wÀ-￿]*/y

type TokType = 'name' | 'op' | 'string' | 'number' | 'fstring'

interface Tok {
  type: TokType
  text: string
  start: number
  end: number
}

interface Edit {
  start: number
  end: number
  text: string
}

/** The value of a Python string literal token, or null when the backend would not read it. */
export function pythonStringValue(token: string): string | null {
  const m = /^([A-Za-z]*)('''|"""|'|")/.exec(token)
  if (!m) return null
  const prefix = m[1].toLowerCase()
  if (prefix.includes('f') || prefix.includes('b')) return null
  const quote = m[2]
  if (!token.endsWith(quote) || token.length < m[0].length + quote.length) return null
  const body = token.slice(m[0].length, token.length - quote.length)
  if (prefix.includes('r')) return body
  let out = ''
  for (let i = 0; i < body.length; i++) {
    const c = body[i]
    if (c !== '\\') {
      out += c
      continue
    }
    const n = body[i + 1]
    if (n === undefined) return null
    const simple: Record<string, string> = {
      '\n': '', '\\': '\\', "'": "'", '"': '"', a: '\x07', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t', v: '\v',
    }
    if (n === '\r') {
      i += body[i + 2] === '\n' ? 2 : 1
      continue
    }
    if (n in simple) {
      out += simple[n]
      i += 1
      continue
    }
    if (n >= '0' && n <= '7') {
      const oct = /^[0-7]{1,3}/.exec(body.slice(i + 1))![0]
      out += String.fromCodePoint(parseInt(oct, 8))
      i += oct.length
      continue
    }
    const width = n === 'x' ? 2 : n === 'u' ? 4 : n === 'U' ? 8 : 0
    if (width > 0) {
      const hex = body.slice(i + 2, i + 2 + width)
      if (!new RegExp(`^[0-9a-fA-F]{${width}}$`).test(hex)) return null
      const cp = parseInt(hex, 16)
      if (cp > 0x10ffff) return null
      out += String.fromCodePoint(cp)
      i += 1 + width
      continue
    }
    // \N{name} needs Python's character names; give up (the code is left alone).
    if (n === 'N') return null
    // An unknown escape keeps its backslash, as Python does.
    out += c
  }
  return out
}

/** Indent width with tabs to multiples of `tab` (Python checks both 8 and 1). */
function indentWidth(ws: string, tab: number): number {
  let col = 0
  for (const c of ws) {
    if (c === '\t') col = Math.floor(col / tab + 1) * tab
    else if (c === '\f') col = 0
    else col += 1
  }
  return col
}

class Lexer {
  readonly tokens: Tok[] = []
  pos = 0
  /** Set when Python's tokenizer would stop here. */
  failed = false
  private depth = 0
  private readonly indents = [0]
  private readonly altIndents = [0]
  private readonly src: string

  constructor(src: string) {
    this.src = src
  }

  run(): void {
    this.atLineStart()
    this.code(false)
  }

  /**
   * Lex code until the end, or (inside an f-string replacement field) until
   * a `}`, `:` or `!` at bracket depth 0, which the caller handles.
   */
  private code(inField: boolean): void {
    const s = this.src
    let local = 0
    while (!this.failed && this.pos < s.length) {
      const c = s[this.pos]
      if (c === '\n' || c === '\r') {
        this.pos += c === '\r' && s[this.pos + 1] === '\n' ? 2 : 1
        if (!inField && this.depth === 0) this.atLineStart()
        continue
      }
      if (c === ' ' || c === '\t' || c === '\f') {
        this.pos += 1
        continue
      }
      if (c === '\\' && (s[this.pos + 1] === '\n' || s[this.pos + 1] === '\r')) {
        this.pos += s[this.pos + 1] === '\r' && s[this.pos + 2] === '\n' ? 3 : 2
        continue
      }
      if (c === '#') {
        while (this.pos < s.length && s[this.pos] !== '\n' && s[this.pos] !== '\r') this.pos += 1
        continue
      }
      if (inField && local === 0 && (c === '}' || c === ':' || (c === '!' && s[this.pos + 1] !== '='))) return
      if (c === '"' || c === "'") {
        this.string(this.pos, '')
        continue
      }
      NAME_RE.lastIndex = this.pos
      const name = NAME_RE.exec(s)
      if (name) {
        const word = name[0]
        const q = s[this.pos + word.length]
        if ((q === '"' || q === "'") && STRING_PREFIXES.has(word.toLowerCase())) {
          this.string(this.pos, word)
          continue
        }
        this.push('name', this.pos, this.pos + word.length)
        continue
      }
      NUMBER_RE.lastIndex = this.pos
      const num = NUMBER_RE.exec(s)
      if (num && num[0].length > 0) {
        this.push('number', this.pos, this.pos + num[0].length)
        continue
      }
      const op = OPS.find(o => s.startsWith(o, this.pos)) ?? c
      if (op === '(' || op === '[' || op === '{') {
        if (inField) local += 1
        else this.depth += 1
      } else if (op === ')' || op === ']' || op === '}') {
        if (inField) local = Math.max(0, local - 1)
        else this.depth = Math.max(0, this.depth - 1)
      }
      this.push('op', this.pos, this.pos + op.length)
    }
  }

  /** Indentation at the start of a logical line (blank and comment lines do not count). */
  private atLineStart(): void {
    const s = this.src
    let i = this.pos
    while (i < s.length && (s[i] === ' ' || s[i] === '\t' || s[i] === '\f')) i += 1
    if (i >= s.length || s[i] === '\n' || s[i] === '\r' || s[i] === '#') return
    const ws = s.slice(this.pos, i)
    const col = indentWidth(ws, 8)
    const alt = indentWidth(ws, 1)
    const top = this.indents[this.indents.length - 1]
    const altTop = this.altIndents[this.altIndents.length - 1]
    if (col === top) {
      if (alt !== altTop) this.failed = true
    } else if (col > top) {
      if (alt <= altTop) this.failed = true
      this.indents.push(col)
      this.altIndents.push(alt)
    } else {
      while (this.indents.length > 1 && col < this.indents[this.indents.length - 1]) {
        this.indents.pop()
        this.altIndents.pop()
      }
      if (col !== this.indents[this.indents.length - 1] || alt !== this.altIndents[this.altIndents.length - 1]) {
        this.failed = true
      }
    }
  }

  /** A string literal starting at `start` (its prefix included). */
  private string(start: number, prefix: string): void {
    const s = this.src
    const q0 = start + prefix.length
    const quote = s.startsWith(s[q0].repeat(3), q0) ? s[q0].repeat(3) : s[q0]
    if (prefix.toLowerCase().includes('f')) {
      this.fstring(start, q0 + quote.length, quote, prefix.toLowerCase().includes('r'))
      return
    }
    let i = q0 + quote.length
    while (i < s.length) {
      if (s[i] === '\\') {
        i += 2
        continue
      }
      if (s.startsWith(quote, i)) {
        this.push('string', start, i + quote.length)
        return
      }
      if (quote.length === 1 && (s[i] === '\n' || s[i] === '\r')) break
      i += 1
    }
    this.failed = true
  }

  /** An f-string: literal parts are skipped, replacement fields are lexed as code. */
  private fstring(start: number, bodyStart: number, quote: string, raw: boolean): void {
    this.push('fstring', start, bodyStart)
    this.pos = bodyStart
    this.fstringBody(quote, raw, false)
  }

  /** Literal text of an f-string (or of a format spec when `inSpec`), with its fields. */
  private fstringBody(quote: string, raw: boolean, inSpec: boolean): void {
    const s = this.src
    while (!this.failed && this.pos < s.length) {
      const c = s[this.pos]
      if (!raw && c === '\\') {
        this.pos += 2
        continue
      }
      if (s.startsWith(quote, this.pos)) {
        if (inSpec) {
          this.failed = true
          return
        }
        this.push('fstring', this.pos, this.pos + quote.length)
        return
      }
      if (quote.length === 1 && (c === '\n' || c === '\r')) break
      if (c === '{' && s[this.pos + 1] === '{' && !inSpec) {
        this.pos += 2
        continue
      }
      if (c === '}') {
        if (inSpec) return
        this.pos += s[this.pos + 1] === '}' ? 2 : 1
        continue
      }
      if (c === '{') {
        this.push('op', this.pos, this.pos + 1)
        this.code(true)
        if (this.failed) return
        if (s[this.pos] === '!') {
          this.push('op', this.pos, this.pos + 1)
          NAME_RE.lastIndex = this.pos
          const conv = NAME_RE.exec(s)
          if (conv) this.push('name', this.pos, this.pos + conv[0].length)
        }
        if (s[this.pos] === ':') {
          this.push('op', this.pos, this.pos + 1)
          this.fstringBody(quote, raw, true)
          if (this.failed) return
        }
        if (s[this.pos] !== '}') {
          this.failed = true
          return
        }
        this.push('op', this.pos, this.pos + 1)
        continue
      }
      this.pos += 1
    }
    this.failed = true
  }

  private push(type: TokType, start: number, end: number): void {
    this.tokens.push({ type, text: this.src.slice(start, end), start, end })
    this.pos = end
  }
}

/**
 * `source` with the path of each literal `ch*()` call replaced by
 * `fix(path)` when that returns a new path (null keeps it). The path is the
 * first argument (or `name=`) when it is a plain one-line string literal;
 * `sl.ch(...)` and other attribute calls are someone else's function.
 * Comments, other strings and the `@attr` sugar are never touched, and the
 * quote style is kept.
 */
export function rewriteChPaths(source: string, fix: (path: string) => string | null): string {
  if (!source || !source.includes('(')) return source
  const lexer = new Lexer(source)
  lexer.run()
  const toks = lexer.tokens
  const edits: Edit[] = []
  for (let k = 0; k < toks.length; k++) {
    const tok = toks[k]
    if (tok.type !== 'string') continue
    const w = (back: number): Tok | undefined => toks[k - back]
    let call: Tok | undefined
    let before: Tok | undefined
    if (w(1)?.type === 'op' && w(1)!.text === '(' && w(2)) {
      call = w(2)
      before = w(3)
    } else if (w(1)?.text === '=' && w(1)!.type === 'op' && w(2)?.text === 'name' && w(3)?.text === '(' && w(3)!.type === 'op' && w(4)) {
      call = w(4)
      before = w(5)
    } else {
      continue
    }
    if (call!.type !== 'name' || !CH_FUNCS.has(call!.text)) continue
    if (before && before.type === 'op' && before.text === '.') continue
    if (/[\n\r]/.test(tok.text)) continue
    const value = pythonStringValue(tok.text)
    if (value === null || !value.includes('/')) continue
    const next = fix(value)
    if (next === null || next === value || UNSAFE_IN_PATH.test(next)) continue
    const m = /^([A-Za-z]*)('''|"""|'|")/.exec(tok.text)!
    edits.push({ start: tok.start, end: tok.end, text: `${m[1]}${m[2]}${next}${m[2]}` })
  }
  if (edits.length === 0) return source
  let out = source
  for (const e of edits.sort((a, b) => b.start - a.start)) {
    out = out.slice(0, e.start) + e.text + out.slice(e.end)
  }
  return out
}
