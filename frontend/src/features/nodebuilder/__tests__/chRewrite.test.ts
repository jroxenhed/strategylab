/**
 * rewriteChPaths, the client twin of backend migrate.rewrite_ch_paths
 * (F435 W7). Each [source, expected] pair below is the backend's own output
 * (Python 3.12 tokenizer) for a fix that renames the path segment `vol` to
 * `vol2`, captured when the twin was written. Covers quote styles and
 * prefixes, `name=`, `sl.ch` and other attribute calls, comments, strings
 * that only look like calls, escapes, f-string fields, line continuations,
 * CRLF, and where the tokenizer stops (unterminated strings, bad dedents).
 * Left out on purpose: `\N{...}` escapes (the client leaves that code alone).
 */
import { describe, expect, it } from 'vitest'
import { pythonStringValue, rewriteChPaths } from '../code/chRewrite'

const fix = (p: string): string | null => {
  const next = p.split('/').map(x => (x === 'vol' ? 'vol2' : x)).join('/')
  return next === p ? null : next
}

const BACKEND_CASES: Array<[string, string]> = [
  ["chf(\"../vol/t\")", "chf(\"../vol2/t\")"],
  ["chf('../vol/t')", "chf('../vol2/t')"],
  ["chf(name=\"../vol/t\")", "chf(name=\"../vol2/t\")"],
  ["chf(name = \"../vol/t\", default=1)", "chf(name = \"../vol2/t\", default=1)"],
  ["sl.ch(\"../vol/t\")", "sl.ch(\"../vol/t\")"],
  ["x.chf(\"../vol/t\") + chf(\"../vol/t\")", "x.chf(\"../vol/t\") + chf(\"../vol2/t\")"],
  ["chf(\"../vol/t\") # chf(\"../vol/t\")", "chf(\"../vol2/t\") # chf(\"../vol/t\")"],
  ["s = \"chf(\\\"../vol/t\\\")\"", "s = \"chf(\\\"../vol/t\\\")\""],
  ["chf(r\"../vol/t\")", "chf(r\"../vol2/t\")"],
  ["chf(u\"../vol/t\")", "chf(u\"../vol2/t\")"],
  ["chf(f\"../vol/t\")", "chf(f\"../vol/t\")"],
  ["chf(b\"../vol/t\")", "chf(b\"../vol/t\")"],
  ["chf(\"\"\"../vol/t\"\"\")", "chf(\"\"\"../vol2/t\"\"\")"],
  ["chf('''../vol/t''')", "chf('''../vol2/t''')"],
  ["chf(\"../vol/t\",\n    default=2)", "chf(\"../vol2/t\",\n    default=2)"],
  ["a = (\n  chf(\"../vol/t\")\n)", "a = (\n  chf(\"../vol2/t\")\n)"],
  ["chf ( \"../vol/t\" )", "chf ( \"../vol2/t\" )"],
  ["chf(\"../v\\x6fl/t\")", "chf(\"../vol2/t\")"],
  ["chf(\"thing\")", "chf(\"thing\")"],
  ["chf(\"/vol/t\")\r\nchi(\"/vol/u\")", "chf(\"/vol2/t\")\r\nchi(\"/vol2/u\")"],
  ["other(\"../vol/t\")", "other(\"../vol/t\")"],
  ["chf(other=\"../vol/t\")", "chf(other=\"../vol/t\")"],
  ["chf(name==\"../vol/t\")", "chf(name==\"../vol/t\")"],
  ["...chf(\"../vol/t\")", "...chf(\"../vol2/t\")"],
  ["@a = chf(\"../vol/t\") * @close", "@a = chf(\"../vol2/t\") * @close"],
  ["x = \"\"\"\nchf(\"../vol/t\")\n\"\"\"\nchf(\"../vol/t\")", "x = \"\"\"\nchf(\"../vol/t\")\n\"\"\"\nchf(\"../vol2/t\")"],
  ["chf(\"../vol/t\") + chf(\"../vol/u", "chf(\"../vol2/t\") + chf(\"../vol/u"],
  ["chf(\"../vol/t\")\n  y = 1\nchf(\"../vol/u\")", "chf(\"../vol2/t\")\n  y = 1\nchf(\"../vol2/u\")"],
  ["if x:\n    chf(\"../vol/t\")\n  chf(\"../vol/u\")", "if x:\n    chf(\"../vol2/t\")\n  chf(\"../vol/u\")"],
  ["f\"{chf('../vol/t')}\"", "f\"{chf('../vol2/t')}\""],
  ["chf(\\\n\"../vol/t\")", "chf(\\\n\"../vol2/t\")"],
  ["chs(\"../vol/t\"); chv(\"/vol/@x\"); chb(\"vol/t\"); ch(\"vol\")", "chs(\"../vol2/t\"); chv(\"/vol2/@x\"); chb(\"vol2/t\"); ch(\"vol\")"],
  ["chf(\"../vol/t\")[0]; chf(\"../vol/t\" \"x\")", "chf(\"../vol2/t\")[0]; chf(\"../vol2/t\" \"x\")"],
  ["é = chf(\"../vol/t\")", "é = chf(\"../vol2/t\")"],
  ["chf(\"../vol/t\")  # é ü", "chf(\"../vol2/t\")  # é ü"],
  ["x = \"é\"; chf(\"../vol/t\")", "x = \"é\"; chf(\"../vol2/t\")"],
  [") chf(\"../vol/t\")", ") chf(\"../vol2/t\")"],
  ["chf(\"../vol/t\")\n)\nchf(\"../vol/u\")", "chf(\"../vol2/t\")\n)\nchf(\"../vol2/u\")"],
  ["chf(\"../vol/t\") $ chf(\"../vol/u\")", "chf(\"../vol2/t\") $ chf(\"../vol2/u\")"],
  ["chf(\"../vol/t\") ? chf(\"../vol/u\")", "chf(\"../vol2/t\") ? chf(\"../vol2/u\")"],
  ["chf(\"../vol/t\") ! chf(\"../vol/u\")", "chf(\"../vol2/t\") ! chf(\"../vol2/u\")"],
  ["chf(\"../vol/t\") != chf(\"../vol/u\")", "chf(\"../vol2/t\") != chf(\"../vol2/u\")"],
  ["if x:\n\tchf(\"../vol/t\")\n        chf(\"../vol/u\")", "if x:\n\tchf(\"../vol2/t\")\n        chf(\"../vol/u\")"],
  ["chf(\"../vol/t\")\n\"\"\"\nchf(\"../vol/u\")", "chf(\"../vol2/t\")\n\"\"\"\nchf(\"../vol/u\")"],
  ["f\"{x:{chf('../vol/t')}}\" + chf(\"../vol/u\")", "f\"{x:{chf('../vol2/t')}}\" + chf(\"../vol2/u\")"],
  ["f\"{{chf('../vol/t')}}\"", "f\"{{chf('../vol/t')}}\""],
  ["f\"{x!r}\" + chf(\"../vol/u\")", "f\"{x!r}\" + chf(\"../vol2/u\")"],
  ["f\"{chf(\"../vol/t\")}\"", "f\"{chf(\"../vol2/t\")}\""],
  ["rf\"{chf('../vol/t')}\\d\"", "rf\"{chf('../vol2/t')}\\d\""],
  ["chf(\"a\\\nb/vol/t\")", "chf(\"a\\\nb/vol/t\")"],
  ["chf(\"../vol/t\",)\n  \n# c\n    chf(\"../vol/u\")", "chf(\"../vol2/t\",)\n  \n# c\n    chf(\"../vol2/u\")"],
  ["chf(\"../vol/t\")\n    \nx\n  y\n", "chf(\"../vol2/t\")\n    \nx\n  y\n"],
  ["x = [\n1,\n  2]\n  chf(\"../vol/u\")", "x = [\n1,\n  2]\n  chf(\"../vol2/u\")"],
  ["chf(\"../v\\ol/t\")", "chf(\"../v\\ol/t\")"],
  ["chf(\"../vol/t\\n\")", "chf(\"../vol/t\\n\")"],
  ["chf(R\"../vol/t\")", "chf(R\"../vol2/t\")"],
  ["chf(Rb\"../vol/t\")", "chf(Rb\"../vol/t\")"],
  ["1.chf(\"../vol/t\")", "1.chf(\"../vol2/t\")"],
  ["1 .chf(\"../vol/t\")", "1 .chf(\"../vol/t\")"],
  ["x = 0x1F; chf(\"../vol/t\")", "x = 0x1F; chf(\"../vol2/t\")"],
  ["3if chf(\"../vol/t\") else 2", "3if chf(\"../vol2/t\") else 2"]
]

describe('rewriteChPaths matches the backend', () => {
  it.each(BACKEND_CASES)('%j', (source, want) => {
    expect(rewriteChPaths(source, fix)).toBe(want)
  })

  it('leaves code with no call or no fix alone (same string back)', () => {
    const src = '@x = @close / 2'
    expect(rewriteChPaths(src, fix)).toBe(src)
    expect(rewriteChPaths('chf("../vol/t")', () => null)).toBe('chf("../vol/t")')
  })

  it('never writes a path that would break the literal', () => {
    expect(rewriteChPaths('chf("../vol/t")', () => '../a"b/t')).toBe('chf("../vol/t")')
  })
})

describe('pythonStringValue', () => {
  it('reads plain, raw and escaped literals; refuses f and b', () => {
    expect(pythonStringValue('"a/b"')).toBe('a/b')
    expect(pythonStringValue("'''a/b'''")).toBe('a/b')
    expect(pythonStringValue('r"a\\d"')).toBe('a\\d')
    expect(pythonStringValue('"a\\x2fb"')).toBe('a/b')
    expect(pythonStringValue('"a\\qb"')).toBe('a\\qb')
    expect(pythonStringValue('f"a/b"')).toBeNull()
    expect(pythonStringValue('b"a/b"')).toBeNull()
  })
})
