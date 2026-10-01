/**
 * Display and bypass flags (spec S16, foundation 4.6).
 *
 * - `D` (flags.setDisplay) puts the display flag on the primary selected
 *   node. Every other node of its network loses it in the same step.
 * - `B` (flags.toggleBypass) toggles bypass on every selected node that can
 *   be bypassed. The new state is the inverse of the primary node's, applied
 *   to all, so a mixed selection becomes uniform.
 * - The dots on a node and in the Inspector header call `clickDisplayFlag`
 *   and `clickBypassFlag`, which act on that one node and never touch the
 *   selection.
 *
 * Each change is one store commit, so one undo step. A change that would
 * do nothing (D on the node that already shows) commits nothing. When a
 * key cannot act, the status bar flashes why (S20).
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { Graph } from '../../../api/nodebuilder'
import { flagProblem, setFlag, setFlags } from '../operations'
import { useNodeBuilderStore, type NodeBuilderState, type NodeBuilderStoreApi } from '../store'
import type { Command } from './index'
import { nodeIsUnsupported } from './unsupported'

/** True when the store holds an editable graph. */
function editing(s: NodeBuilderState): boolean {
  return s.graph != null && !s.graph.readOnly
}

function nameOf(graph: Graph, id: string): string {
  return graph.nodes[id]?.name || id
}

/** The selected graph nodes, and the primary one (the last clicked). */
function selectedNodes(s: NodeBuilderState): { ids: string[]; primary: string | null } {
  const graph = s.graph
  if (!graph) return { ids: [], primary: null }
  const ids = s.selectedNodeIds.filter(id => id in graph.nodes)
  const primary = s.selectedNodeId && ids.includes(s.selectedNodeId)
    ? s.selectedNodeId
    : ids[ids.length - 1] ?? null
  return { ids, primary }
}

/**
 * Make `nodeId` the display node of its network. Returns the reason when
 * it cannot (and flashes it), or null. The lit dot does nothing (Houdini).
 */
export function clickDisplayFlag(nodeId: string, store: NodeBuilderStoreApi = useNodeBuilderStore): string | null {
  const s = store.getState()
  const graph = s.graph
  if (!graph || graph.readOnly) return 'Read-only graph'
  const problem = flagProblem(graph, nodeId, 'display')
  if (problem) {
    s.showFlash(problem)
    return problem
  }
  if (graph.nodes[nodeId].display) return null
  s.commit(`display ${nameOf(graph, nodeId)}`, g => setFlag(g, nodeId, 'display', true))
  return null
}

/** Toggle bypass on one node. Returns the reason when it cannot (and flashes it), or null. */
export function clickBypassFlag(nodeId: string, store: NodeBuilderStoreApi = useNodeBuilderStore): string | null {
  const s = store.getState()
  const graph = s.graph
  if (!graph || graph.readOnly) return 'Read-only graph'
  const node = graph.nodes[nodeId]
  // Turning bypass off always works (an older graph may carry a stray flag).
  const problem = node?.bypass ? null : flagProblem(graph, nodeId, 'bypass')
  if (problem) {
    s.showFlash(problem)
    return problem
  }
  s.commit(`bypass ${nameOf(graph, nodeId)}`, g => setFlag(g, nodeId, 'bypass', !node.bypass))
  return null
}

export const commands: Command[] = [
  {
    id: 'flags.setDisplay',
    label: 'Display flag',
    keys: ['d'],
    scope: 'canvas',
    menu: 'node',
    when: editing,
    checked: s => {
      const { primary } = selectedNodes(s)
      return primary != null && s.graph?.nodes[primary]?.display === true
    },
    run({ store }) {
      const s = store.getState()
      const { primary } = selectedNodes(s)
      if (!primary) {
        s.showFlash('Select a node first')
        return
      }
      clickDisplayFlag(primary, store)
    },
  },
  {
    id: 'flags.toggleBypass',
    label: 'Bypass',
    keys: ['b'],
    scope: 'canvas',
    menu: 'node',
    when: editing,
    checked: s => {
      const { primary } = selectedNodes(s)
      return primary != null && s.graph?.nodes[primary]?.bypass === true
    },
    run({ store }) {
      const s = store.getState()
      const graph = s.graph
      const { ids, primary } = selectedNodes(s)
      if (!graph || !primary) {
        s.showFlash('Select a node first')
        return
      }
      // Bypassed nodes can always be switched back on; others must allow it.
      // An unsupported node (S13) is never bypassed (UX-14).
      const allowed = ids.filter(id => graph.nodes[id].bypass
        || (flagProblem(graph, id, 'bypass') == null && !nodeIsUnsupported(graph, id)))
      if (allowed.length === 0) {
        s.showFlash(flagProblem(graph, primary, 'bypass')
          ?? (nodeIsUnsupported(graph, primary) ? 'Unsupported nodes cannot be bypassed' : 'Select a node first'))
        return
      }
      // The primary decides the new state; if it cannot be bypassed itself,
      // the first node that can decides.
      const lead = allowed.includes(primary) ? primary : allowed[0]
      const value = !graph.nodes[lead].bypass
      const label = allowed.length === 1
        ? `bypass ${nameOf(graph, allowed[0])}`
        : `bypass ${allowed.length} nodes`
      s.commit(label, g => setFlags(g, allowed, 'bypass', value))
    },
  },
]
