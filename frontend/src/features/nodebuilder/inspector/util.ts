/**
 * Small helpers shared by the Inspector views.
 */

import { createContext, useContext, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useShallow } from 'zustand/react/shallow'
import type { Graph } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import type { ParamSpec } from '../catalog'
import { CATS, type CatKey } from '../categories'
import { getActiveCanvas, listCommands, type Command } from '../commands'
import { useNodeBuilderStore } from '../store'
import { catalogEntry } from '../streamLabels'

/**
 * Where the Inspector's graph comes from. `null` (the default): the store
 * graph, read through narrow store selectors. `{ graph }`: the read-only
 * view's graph (not in the store), published by the canvas (screen.ts).
 * The Inspector root provides it (Inspector.tsx); outside an Inspector the
 * hooks below read the store.
 */
export interface InspectorSource {
  graph: Graph | null
}

export const InspectorSourceContext = createContext<InspectorSource | null>(null)

const NOTHING = Symbol('nothing')

/**
 * A value derived from the Inspector's graph, re-rendering the caller only
 * when it changes (shallow compare: a primitive, an array of the same
 * items, an object with the same values). Use this, not the whole graph,
 * in anything drawn for one node, so an edit elsewhere in the graph does
 * not re-render it (IP-2). `pick` gets null when no graph is loaded.
 */
export function useInspectorSelect<T>(pick: (graph: Graph | null) => T): T {
  const source = useContext(InspectorSourceContext)
  const fromStore = useNodeBuilderStore(useShallow(s => (source ? NOTHING : pick(s.graph))))
  return source ? pick(source.graph) : (fromStore as T)
}

/** True when the Inspector's graph can be edited (an editable store graph). */
export function useInspectorEditable(): boolean {
  const source = useContext(InspectorSourceContext)
  const storeEditable = useNodeBuilderStore(s => s.graph != null && !s.graph.readOnly)
  return source ? false : storeEditable
}

/**
 * The graph the Inspector shows: the store graph while editing, else the
 * read-only view's graph on screen (screen.ts; that graph is not in the
 * store). `editable` is true only for an editable store graph.
 * Re-renders on every graph change: views drawn for one node use
 * `useInspectorSelect` instead.
 * Reads the store only, never React Flow, so the panel renders even while
 * the canvas is hidden.
 */
export function useInspectorGraph(): { graph: Graph | null; editable: boolean } {
  const source = useContext(InspectorSourceContext)
  const storeGraph = useNodeBuilderStore(s => (source ? null : s.graph))
  if (source) return { graph: source.graph, editable: false }
  return { graph: storeGraph, editable: storeGraph != null && !storeGraph.readOnly }
}

/** The Inspector's graph now, not reactive (event handlers, render-time checks while editing). */
export function inspectorGraphNow(source: InspectorSource | null): Graph | null {
  return source ? source.graph : useNodeBuilderStore.getState().graph
}

/** The category key of a node type, or null for an unknown type. */
export function categoryOf(nodeType: string | undefined): string | null {
  return catalogEntry(nodeType)?.cat ?? null
}

/** Glyph for a category (`?` for an unknown one). */
export function glyphOf(cat: string | null): string {
  if (cat === 'network') return 'N'
  return cat && cat in CATS ? CATS[cat as CatKey].glyph : '?'
}

/** CSS vars that color chips and the glyph chip in a category. */
export function catVars(cat: string | null): CSSProperties {
  if (!cat) return {}
  return { ['--cat' as string]: `var(--nb-cat-${cat})`, ['--tint' as string]: `var(--nb-tint-${cat})` } as CSSProperties
}

/** The newest registered command bound to any of these chords, or null. */
export function commandForKeys(chords: readonly string[]): Command | null {
  const all = listCommands()
  for (let i = all.length - 1; i >= 0; i--) {
    if (all[i].keys?.some(k => chords.includes(k))) return all[i]
  }
  return null
}

/** `⌘` on a Mac, else `Ctrl`. */
export function modKeyLabel(): string {
  try {
    const p = (globalThis.navigator?.platform ?? '') + ' ' + (globalThis.navigator?.userAgent ?? '')
    return /Mac|iPhone|iPad/.test(p) ? '⌘' : 'Ctrl'
  } catch {
    return 'Ctrl'
  }
}

/** A param value as plain text (read-only rows, mixed checks). */
export function valueText(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (Array.isArray(v)) return v.join(', ')
  // W7 (S44 read-only): an expression shows as `= <text>`.
  if (typeof v === 'object' && typeof (v as { expr?: unknown }).expr === 'string') return `= ${(v as { expr: string }).expr}`
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/** True when two param values are the same (lists compared by content). */
export function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (a == null || b == null) return false
  if (typeof a === 'object' || typeof b === 'object') return JSON.stringify(a) === JSON.stringify(b)
  return false
}

/** Give the canvas keyboard focus again (Esc in a field, end of a rename). */
export function focusCanvas(): void {
  try {
    getActiveCanvas()?.focus()
  } catch {
    // No canvas mounted.
  }
}

/** Set a text field's value the way typing would, so React's onChange runs. */
function typeInto(input: HTMLInputElement, text: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, text)
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

/**
 * Up/Down in a number field steps the value by the spec's `step`
 * (Shift x10, Alt x0.1 except on an int), clamped to min/max. The new text is typed into the
 * field, so Enter or blur commits it like any other edit (S14).
 */
export function stepNumberField(e: ReactKeyboardEvent, spec: ParamSpec | undefined): void {
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
  const input = e.target
  if (!(input instanceof HTMLInputElement) || input.inputMode !== 'decimal') return
  const current = Number(input.value.trim())
  if (input.value.trim() === '' || !Number.isFinite(current)) return
  const isInt = spec?.type === 'int'
  let step = spec?.step ?? 1
  if (e.shiftKey) step *= 10
  // Alt's fine step has no meaning for an int: it keeps the plain step.
  else if (e.altKey && !isInt) step *= 0.1
  let next = current + (e.key === 'ArrowUp' ? step : -step)
  next = Number(next.toFixed(10))
  if (isInt) next = Math.round(next)
  if (typeof spec?.min === 'number') next = Math.max(spec.min, next)
  if (typeof spec?.max === 'number') next = Math.min(spec.max, next)
  e.preventDefault()
  typeInto(input, String(next))
}

/**
 * A typed number for a param: an int is rounded, and the value is kept
 * inside the spec's min and max (FC-8), as the single-node arrows do.
 */
export function coerceParamNumber(n: number, spec: Pick<ParamSpec, 'type' | 'min' | 'max'>): number {
  let v = spec.type === 'int' ? Math.round(n) : n
  if (typeof spec.min === 'number') v = Math.max(spec.min, v)
  if (typeof spec.max === 'number') v = Math.min(spec.max, v)
  return v
}

/** `1 error`, `2 warnings`, or nothing. */
export function diagnosticsCountText(list: readonly Diagnostic[]): { text: string; kind?: 'error' | 'warn' } {
  const errors = list.filter(d => d.severity === 'error').length
  const warnings = list.filter(d => d.severity === 'warning').length
  if (errors) return { text: `${errors} ${errors === 1 ? 'error' : 'errors'}`, kind: 'error' }
  if (warnings) return { text: `${warnings} ${warnings === 1 ? 'warning' : 'warnings'}`, kind: 'warn' }
  return { text: '' }
}
