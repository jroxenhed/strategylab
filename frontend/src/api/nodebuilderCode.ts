/**
 * Node builder code client (F435 W7, plan "Wave 7" contracts, specs S44 to S49).
 *
 * POST /api/nodebuilder/parse_code checks one piece of code while the user
 * types. The server runs its `prepare()` step only (the `@name` sugar, the
 * Python parse, the `ch*()` scan and `compile()`); it never runs the code.
 * It answers with the spare params the code declares, the attributes it
 * reads and writes, and its diagnostics.
 *
 * GET /api/nodebuilder/code_capabilities says whether code nodes are on
 * (`SL_CODE_NODES`), the limits, and the `sl` helper list for completion.
 *
 * Positions: the server sends `line` 1-based and `col` 0-based, in
 * characters of the user's text, as Python reports them. `toMonacoRange`
 * is the one place that turns them into Monaco's 1-based columns.
 */

import { api } from './client'
import type { Graph, SpareParamSpec } from './nodebuilder'
import type { Diagnostic } from './nodebuilderValidate'

export type { SpareParamSpec } from './nodebuilder'

export type CodeContext = 'expr' | 'node_code' | 'wrangle'

/** One attribute the code reads or writes. `dtype` is 'any' for an unannotated write. */
export interface AttrDecl {
  name: string
  class: 'point' | 'detail'
  dtype: string
}

export interface ParseCodeResponse {
  ok: boolean
  params: SpareParamSpec[]
  reads: AttrDecl[]
  writes: AttrDecl[]
  /** The type the code returns when the server knows it; null in W7 (parse_code never runs code). */
  result_type: string | null
  /** line 1-based, col 0-based. */
  diagnostics: Diagnostic[]
}

export interface CodeFunctionInfo {
  name: string
  signature: string
  returns: string
  doc: string
}

export interface CodeCapabilities {
  enabled: boolean
  language: 'python'
  limits: { max_source_bytes: number; default_lookback_bars: number; cook_timeout_s: { bot: number; backtest: number } }
  modules: string[]
  functions: CodeFunctionInfo[]
  leaked_cooks: number
}

export interface ParseCodeBody {
  code: string
  context: CodeContext
  expected: { type: string } | null
  graph: Graph | null
  node_id: string | null
}

function readDecls(raw: unknown): AttrDecl[] {
  if (!Array.isArray(raw)) return []
  const out: AttrDecl[] = []
  for (const a of raw) {
    if (!a || typeof a !== 'object') continue
    const r = a as Record<string, unknown>
    if (typeof r.name !== 'string' || !r.name) continue
    out.push({
      name: r.name,
      class: r.class === 'detail' ? 'detail' : 'point',
      dtype: typeof r.dtype === 'string' ? r.dtype : 'any',
    })
  }
  return out
}

const SPARE_TYPES: ReadonlySet<string> = new Set(['float', 'int', 'string', 'bool', 'vector'])

function readSpecs(raw: unknown): SpareParamSpec[] {
  if (!Array.isArray(raw)) return []
  const out: SpareParamSpec[] = []
  for (const p of raw) {
    if (!p || typeof p !== 'object') continue
    const r = p as Record<string, unknown>
    if (typeof r.name !== 'string' || !r.name || typeof r.type !== 'string' || !SPARE_TYPES.has(r.type)) continue
    out.push({
      name: r.name,
      type: r.type as SpareParamSpec['type'],
      default: r.default ?? null,
      min: typeof r.min === 'number' ? r.min : null,
      max: typeof r.max === 'number' ? r.max : null,
      options: Array.isArray(r.options) ? r.options.filter((o): o is string => typeof o === 'string') : null,
      label: typeof r.label === 'string' && r.label ? r.label : r.name,
    })
  }
  return out
}

/**
 * Check one piece of code (never runs it). Pass `signal` to cancel it when a
 * newer keystroke supersedes it. Throws on a network error or a non-2xx reply.
 */
export async function parseCode(body: ParseCodeBody, signal?: AbortSignal): Promise<ParseCodeResponse> {
  const { data } = await api.post<ParseCodeResponse>('/api/nodebuilder/parse_code', body, { signal })
  const diagnostics = Array.isArray(data?.diagnostics) ? data.diagnostics : []
  return {
    ok: typeof data?.ok === 'boolean' ? data.ok : !diagnostics.some(d => d.severity === 'error'),
    params: readSpecs(data?.params),
    reads: readDecls(data?.reads),
    writes: readDecls(data?.writes),
    result_type: null,
    diagnostics,
  }
}

/** Whether code nodes are on, the limits and the `sl` helpers. Fetched once per mount (S49: never polled). */
export async function getCodeCapabilities(): Promise<CodeCapabilities> {
  const { data } = await api.get<CodeCapabilities>('/api/nodebuilder/code_capabilities')
  return {
    enabled: data?.enabled !== false,
    language: 'python',
    limits: {
      max_source_bytes: data?.limits?.max_source_bytes ?? 8192,
      default_lookback_bars: data?.limits?.default_lookback_bars ?? 500,
      cook_timeout_s: {
        bot: data?.limits?.cook_timeout_s?.bot ?? 10,
        backtest: data?.limits?.cook_timeout_s?.backtest ?? 60,
      },
    },
    modules: Array.isArray(data?.modules) ? data.modules : [],
    functions: Array.isArray(data?.functions)
      ? data.functions.filter(f => f && typeof f.name === 'string' && typeof f.signature === 'string')
      : [],
    leaked_cooks: typeof data?.leaked_cooks === 'number' ? data.leaked_cooks : 0,
  }
}

/** A Monaco range: 1-based lines and 1-based columns. */
export interface MonacoRange {
  startLineNumber: number
  startColumn: number
  endLineNumber: number
  endColumn: number
}

/**
 * Convert one diagnostic to a Monaco range: startLineNumber = line,
 * startColumn = col + 1, endLineNumber = end_line ?? line,
 * endColumn = (end_col ?? col + 1) + 1, and at least startColumn + 1 on one
 * line. A diagnostic with no line marks line 1 from column 1.
 */
export function toMonacoRange(d: Pick<Diagnostic, 'line' | 'col' | 'end_line' | 'end_col'>): MonacoRange {
  const line = typeof d.line === 'number' && d.line >= 1 ? d.line : 1
  const col = typeof d.col === 'number' && d.col >= 0 ? d.col : 0
  const startColumn = col + 1
  const endLineNumber = typeof d.end_line === 'number' && d.end_line >= line ? d.end_line : line
  let endColumn = (typeof d.end_col === 'number' && d.end_col >= 0 ? d.end_col : col + 1) + 1
  if (endLineNumber === line && endColumn < startColumn + 1) endColumn = startColumn + 1
  return { startLineNumber: line, startColumn, endLineNumber, endColumn }
}

/** The `<code> at <line>:<col>: <message>` text of S44 (no position: `<code>: <message>`). */
export function diagnosticText(d: Pick<Diagnostic, 'code' | 'line' | 'col' | 'message'>): string {
  if (typeof d.line === 'number') return `${d.code} at ${d.line}:${d.col ?? 0}: ${d.message}`
  return `${d.code}: ${d.message}`
}
