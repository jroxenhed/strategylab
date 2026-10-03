/**
 * Sections of the Inspector's node view (spec S14), as a registry.
 *
 * The built-in sections (Parameters, Stream, Diagnostics) register here the
 * same way later waves add theirs, so W7 can put its code editors in the
 * Inspector without editing Inspector.tsx:
 *
 *   registerInspectorSection({
 *     id: 'code', title: 'Code', order: 20,
 *     when: ({ node }) => hasCode(node),
 *     Component: CodeSection,
 *   })
 *
 * Lower `order` comes first: parameters 10, code 20 (W7), stream 30,
 * diagnostics 40, notes 50. Whether a section is expanded is kept per id in
 * `nb.inspector.sections` (state.ts). A section body only renders while the
 * section is expanded.
 */

import { useSyncExternalStore, type ComponentType } from 'react'
import type { GraphNode } from '../../../api/nodebuilder'
import { KeyedStack } from '../keyedStack'

/**
 * What every node section gets. A section renders again only when one of
 * these changes (it is memoized on them), so an edit to another node does
 * not re-render it (IP-2). A section that needs more of the graph reads it
 * with a narrow hook from inspector/util.ts: `useInspectorSelect(g => ...)`
 * (re-renders only when the picked value changes) or, for the whole graph,
 * `useInspectorGraph()`. Both work in the read-only view too.
 */
export interface InspectorSectionProps {
  nodeId: string
  /** The node (same object until the node itself changes). */
  node: GraphNode
  /** False for a read-only graph or an unsupported node (S13): show values as text, offer no edits. */
  editable: boolean
}

export interface InspectorSection {
  /** Also the key in the persisted sections map and the test id suffix. */
  id: string
  /** Shown in caps in the section header. */
  title: string
  order: number
  /** Leave the section out for this node (e.g. Code on a node with no code). */
  when?(props: InspectorSectionProps): boolean
  /** Optional right-side header text (`3`, `1 error`). */
  Count?: ComponentType<InspectorSectionProps>
  /**
   * The key its open state is kept under (default: `id`). W7's Code section
   * keeps a separate state for nodes with and without code.
   */
  stateKey?(props: InspectorSectionProps): string
  /** Open state before the user ever toggles it (default: the built-in default, open). */
  defaultOpen?(props: InspectorSectionProps): boolean
  Component: ComponentType<InspectorSectionProps>
}

// Same override rule as every registry (keyedStack.ts, EA-12): a section
// with an id already there overrides it, and removing the override brings
// the earlier one back.
const registry = new KeyedStack<InspectorSection>()
let sections: InspectorSection[] = []
const listeners = new Set<() => void>()

function changed(): void {
  sections = registry.values().sort((a, b) => a.order - b.order)
  for (const l of [...listeners]) l()
}

/** Add a section (one with the same id is overridden until this one is removed). Returns a function that removes it. */
export function registerInspectorSection(section: InspectorSection): () => void {
  const remove = registry.push(section.id, section)
  changed()
  return () => {
    if (remove()) changed()
  }
}

export function listInspectorSections(): readonly InspectorSection[] {
  return sections
}

function subscribe(l: () => void): () => void {
  listeners.add(l)
  return () => { listeners.delete(l) }
}

/** The registered sections, re-rendering the caller when they change. */
export function useInspectorSections(): readonly InspectorSection[] {
  return useSyncExternalStore(subscribe, () => sections)
}
