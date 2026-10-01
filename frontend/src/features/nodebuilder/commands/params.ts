/**
 * The param-row menu (S19 "Param row", S14): right-click a param row on a
 * node card or in the Inspector.
 *
 * - `params.reset` (Set to default): writes the catalog default. One
 *   commit through `updateNodeParams`, so one undo step.
 * - `params.copyValue` (Copy value): the value as text on the system
 *   clipboard. Works in the read-only view too.
 *
 * Later rows (Use expression `=` W7, Promote to parent… W6) join the menu
 * through `menuSlot: 'param'`. "Add slider" is not a row: the Inspector
 * already draws a slider under every number row with a min and a max.
 *
 * The param comes from the open menu (contextMenuModel.getMenuTarget), so
 * these commands have no keys.
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { Graph } from '../../../api/nodebuilder'
import { getMenuTarget } from '../contextMenuModel'
import { getScreenGraph } from '../screen'
import type { NodeBuilderState } from '../store'
import { paramSpecsOf } from '../streamLabels'
import type { Command } from './index'

/** The graph the menu was opened on: the store graph while editing, else the one on screen. */
function graphOf(s: NodeBuilderState): Graph | null {
  return s.graph ?? getScreenGraph().graph
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (a == null || b == null) return false
  return JSON.stringify(a) === JSON.stringify(b)
}

/** The target param's node, spec and current value, or null. */
function targetParam(s: NodeBuilderState) {
  const t = getMenuTarget()
  const g = graphOf(s)
  if (!t || !g) return null
  const node = g.nodes[t.nodeId]
  if (!node) return null
  const spec = paramSpecsOf(node.type).find(p => p.name === t.param)
  return { t, node, spec, value: node.params[t.param] ?? spec?.default, graph: g }
}

function resetProblem(s: NodeBuilderState): string | null {
  const p = targetParam(s)
  if (!p) return 'No param'
  if (!s.graph || s.graph.readOnly || p.graph !== s.graph) return 'Read-only graph'
  if (!p.spec || p.spec.default === undefined) return 'No default for this param'
  if (sameValue(p.node.params[p.t.param], p.spec.default)) return 'Already the default'
  return null
}

/** A param value as plain text. */
export function paramValueText(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export const commands: Command[] = [
  {
    id: 'params.reset',
    label: 'Set to default',
    menu: 'param',
    when: s => resetProblem(s) === null,
    disabledReason: resetProblem,
    run: ({ store }) => {
      const s = store.getState()
      const p = targetParam(s)
      if (!p || resetProblem(s) !== null) return false
      s.updateNodeParams(p.t.nodeId, { [p.t.param]: p.spec!.default })
    },
  },
  {
    id: 'params.copyValue',
    label: 'Copy value',
    menu: 'param',
    readOnlyOk: true,
    when: s => targetParam(s) !== null,
    disabledReason: s => (targetParam(s) ? null : 'No param'),
    run: ({ store }) => {
      const p = targetParam(store.getState())
      if (!p) return false
      const text = paramValueText(p.value)
      try {
        void globalThis.navigator?.clipboard?.writeText(text)?.catch?.(() => {})
      } catch {
        // No clipboard access (an insecure page): nothing to copy into.
      }
      store.getState().showFlash(`Copied ${p.t.param}`)
    },
  },
]
