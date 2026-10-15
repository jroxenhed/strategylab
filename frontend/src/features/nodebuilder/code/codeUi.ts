/**
 * Small helpers of the code UI (F435 W7, specs S44 to S49): texts, keys and
 * hooks shared by the code components. Kept out of the .tsx files so those
 * export components only (fast refresh).
 */

import type { GraphNode, SpareParamSpec } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import type { ParamSpec, ParamTypeSpec } from '../catalog'
import { useNodeBuilderStore } from '../store'
import { setSectionOpen, toggleInspector, useInspectorUi } from '../inspector/state'
import { NETWORK_TYPES, BOUNDARY_TYPES } from '../rfMapping'
import { WRANGLE_TYPE } from './codeOps'
import { markCodeAdded, requestCodeFocus } from './codeStore'

const NO_SPECS: SpareParamSpec[] = []

/** The dot at the right end of an expression field. */
export type ExprStatus = 'none' | 'ok' | 'error' | 'warn'

/** The dot for a parse answer (none while a newer parse is pending). */
export function exprStatus(diagnostics: readonly Diagnostic[] | null, pending: boolean): ExprStatus {
  if (pending || !diagnostics) return 'none'
  if (diagnostics.some(d => d.severity === 'error')) return 'error'
  if (diagnostics.some(d => d.severity === 'warning')) return 'warn'
  return 'ok'
}

/** `code · 3 lines` */
export function codeLinesText(n: number): string {
  return `code · ${n} line${n === 1 ? '' : 's'}`
}

/** `… +4 lines` */
export function moreLinesText(n: number): string {
  return `… +${n} line${n === 1 ? '' : 's'}`
}

/** The Inspector state key of the Code section for a node (see CodeSection.tsx). */
export function codeSectionKey(hasCodeNow: boolean, wrangle: boolean): string {
  if (wrangle) return 'code.wrangle'
  return hasCodeNow ? 'code' : 'code.off'
}

/**
 * Select a node, open the Inspector with its Code section expanded, and
 * focus the editor at `line` (1-based).
 */
export function openCodeInInspector(nodeId: string, line = 1): void {
  const s = useNodeBuilderStore.getState()
  const node = s.graph?.nodes[nodeId]
  if (s.selectedNodeIds.length !== 1 || s.selectedNodeIds[0] !== nodeId) {
    s.setSelection({ nodeIds: [nodeId], primary: nodeId })
  }
  toggleInspector(true)
  const wrangle = node?.type === 'wrangle'
  // Same rule as the section's own state key: whitespace-only code counts as none.
  setSectionOpen(codeSectionKey(!!(node?.code && node.code.trim()), wrangle), true)
  requestCodeFocus(nodeId, line)
}

/** True for nodes that may carry a code block: not networks or their boundary nodes. */
export function canHaveCode(node: Pick<GraphNode, 'type'>): boolean {
  return !NETWORK_TYPES.has(node.type) && !BOUNDARY_TYPES.has(node.type)
}

/** `Checked 0.3 s ago` */
export function checkedAgoText(ms: number): string {
  return `Checked ${(Math.max(0, ms) / 1000).toFixed(1)} s ago`
}

/** `4 of 8 KB` */
export function sizeText(bytes: number, max: number): string {
  const kb = (n: number) => (n >= 1024 ? Number((n / 1024).toFixed(1)) : Number((n / 1024).toFixed(1)))
  return `${kb(bytes)} of ${Math.round(max / 1024)} KB`
}

/** The section header tag (S45 copy). */
export function codeTagText(code: string, diagnostics: readonly Diagnostic[] | null, reads: string[], writes: string[]): { text: string; kind: 'ok' | 'error' | 'warn' | 'off' } {
  if (!code.trim()) return { text: 'off', kind: 'off' }
  const errors = diagnostics?.filter(d => d.severity === 'error').length ?? 0
  const warnings = diagnostics?.filter(d => d.severity === 'warning').length ?? 0
  if (errors) return { text: `${errors} error${errors === 1 ? '' : 's'}`, kind: 'error' }
  if (warnings) return { text: `${warnings} warning${warnings === 1 ? '' : 's'}`, kind: 'warn' }
  const parts = ['ok']
  if (reads.length) parts.push(`reads ${reads.join(' ')}`)
  if (writes.length) parts.push(`writes ${writes.join(' ')}`)
  return { text: parts.join(' · '), kind: 'ok' }
}

/** Format (indent): tabs become 4 spaces and trailing spaces go. */
export function formatCode(code: string): string {
  return code.split('\n').map(l => l.replace(/\t/g, '    ').replace(/\s+$/, '')).join('\n')
}


/** Open the Code section of a node for a new block (node menu `Add code block`). */
export function addCodeBlock(nodeId: string): void {
  markCodeAdded(nodeId)
}

/** True while this node's Code section is expanded (spare params then live there, S48). */
export function useCodeSectionOpen(node: GraphNode | null | undefined): boolean {
  return useInspectorUi(s => {
    if (!node || !canHaveCode(node)) return false
    const key = codeSectionKey(!!(node.code && node.code.trim()), node.type === WRANGLE_TYPE)
    if (key in s.sections) return s.sections[key]
    return node.type === WRANGLE_TYPE || !!(node.code && node.code.trim())
  })
}

/** The `ch*` function a spec came from (for the tooltip). */
function chFn(type: SpareParamSpec['type']): string {
  switch (type) {
    case 'float': return 'chf'
    case 'int': return 'chi'
    case 'bool': return 'chb'
    case 'vector': return 'chv'
    default: return 'chs'
  }
}

/** `Spare parameter from chf("threshold") in this node's code.` */
export function spareTooltip(spec: Pick<SpareParamSpec, 'name' | 'type'>): string {
  return `Spare parameter from ${chFn(spec.type)}("${spec.name}") in this node's code.`
}

/** The catalog-style spec a spare row draws with. */
export function spareParamSpec(spec: SpareParamSpec): ParamSpec {
  const type: ParamSpec['type'] = spec.type === 'float' ? 'number'
    : spec.type === 'int' ? 'int'
      : spec.type === 'bool' ? 'bool'
        : spec.options && spec.options.length ? 'select' : 'string'
  const out: ParamSpec = { name: spec.name, type, label: spec.label || spec.name, default: spec.default }
  if (typeof spec.min === 'number') out.min = spec.min
  if (typeof spec.max === 'number') out.max = spec.max
  if (spec.options && spec.options.length) out.options = spec.options
  return out
}

/** The ParamRow input type of a spare row. */
export function spareTypeSpec(spec: SpareParamSpec): ParamTypeSpec {
  if (spec.type === 'float' || spec.type === 'int') return { type: 'number' }
  if (spec.options && spec.options.length) return { type: 'select', options: spec.options }
  return { type: 'string' }
}

/** The spare params of a node in the store (a stable empty list when none). */
export function useSpareSpecs(nodeId: string, node?: GraphNode | null): SpareParamSpec[] {
  const fromStore = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.spare_params)
  return (node ? node.spare_params : fromStore) ?? NO_SPECS
}

/** `+2 more in Inspector` */
export function moreSpareText(n: number): string {
  return `+${n} more in Inspector`
}


/** `Wrangle spread_z, writes @spread and @spread_z` */
export function wrangleAriaLabel(name: string, writes: readonly string[]): string {
  if (writes.length === 0) return `Wrangle ${name}, writes nothing`
  const list = writes.length === 1 ? writes[0] : `${writes.slice(0, -1).join(', ')} and ${writes[writes.length - 1]}`
  return `Wrangle ${name}, writes ${list}`
}

