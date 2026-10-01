/**
 * Selection keys (foundation 6.2, W3 review fix UX-13).
 *
 * - `Esc` (selection.clear): clears the selection. Menus, the Tab menu, a
 *   wire drag and text fields take Esc first (they handle it themselves),
 *   so this is the last of the spec's Esc priorities.
 * - `Cmd+A` (selection.all): selects every node in the network on screen.
 *   In a text field Cmd+A keeps selecting the text (canvas keys do not run
 *   in fields).
 * - Arrow keys (edit.nudge): move the selection 1px, Shift+Arrow 24px.
 *   A burst of presses (a held key, or presses less than a second apart
 *   with nothing else committed in between) is ONE undo step.
 *
 * Esc and Cmd+A also work in the read-only view (they edit nothing).
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { Graph } from '../../../api/nodebuilder'
import { moveNodes as opMoveNodes } from '../operations'
import type { NodeBuilderState } from '../store'
import { annotationsOf, opMoveAnnotations } from '../store/annotations'
import { currentParentId } from '../store/view'
import type { Command, CommandCtx } from './index'

/** The coalesce key of a nudge burst (store/graph.ts CommitOptions). */
export const NUDGE_COALESCE = 'edit.nudge'

function hasSelection(s: NodeBuilderState): boolean {
  return s.selectedNodeIds.length > 0 || s.selectedWireIds.length > 0 || s.selectedAnnotationIds.length > 0
}

/** The graph the key acts on: the store graph, else the read-only one on screen. */
function graphOf(ctx: CommandCtx): Graph | null {
  return ctx.store.getState().graph ?? ctx.canvas?.graph() ?? null
}

const NUDGES: ReadonlyArray<[key: string, dx: number, dy: number]> = [
  ['arrowleft', -1, 0],
  ['arrowright', 1, 0],
  ['arrowup', 0, -1],
  ['arrowdown', 0, 1],
]

/** Big nudge (Shift+Arrow): one snap-grid cell. */
export const NUDGE_BIG = 24

function nudge(ctx: CommandCtx, dx: number, dy: number): boolean {
  const s = ctx.store.getState()
  const g = s.graph
  if (!g || g.readOnly) return false
  const nodeIds = s.selectedNodeIds.filter(id => id in g.nodes)
  const { boxes, notes } = annotationsOf(g)
  const annIds = new Set(s.selectedAnnotationIds)
  const moves = new Map<string, { x: number; y: number }>()
  for (const a of [...boxes, ...notes]) {
    if (annIds.has(a.id)) moves.set(a.id, { x: a.rect[0] + dx, y: a.rect[1] + dy })
  }
  if (nodeIds.length === 0 && moves.size === 0) return false
  const label = nodeIds.length + moves.size === 1 ? 'nudge' : `nudge ${nodeIds.length + moves.size} items`
  s.commit(label, graph => {
    // Selected nodes move once, even when their box moves too.
    const moved = opMoveNodes(graph, nodeIds, [dx, dy])
    return opMoveAnnotations(moved, moves, new Set(nodeIds))
  }, { coalesce: NUDGE_COALESCE })
  return true
}

export const commands: Command[] = [
  {
    id: 'selection.clear',
    label: 'Clear selection',
    keys: ['escape'],
    scope: 'canvas',
    readOnlyOk: true,
    when: hasSelection,
    run({ store }) {
      store.getState().setSelection({ nodeIds: [], wireIds: [], annotationIds: [] })
    },
  },
  {
    id: 'selection.all',
    label: 'Select all',
    keys: ['mod+a'],
    scope: 'canvas',
    readOnlyOk: true,
    run(ctx) {
      const g = graphOf(ctx)
      if (!g) return false
      const s = ctx.store.getState()
      const parent = currentParentId(s, g)
      const ids = Object.values(g.nodes).filter(n => (n.parent ?? null) === parent).map(n => n.id)
      // Nothing to select: still handled, so the browser does not select the page text.
      if (ids.length === 0) return
      s.setSelection({ nodeIds: ids, primary: s.selectedNodeId ?? ids[0] })
    },
  },
  {
    id: 'edit.nudge',
    label: 'Nudge',
    keys: NUDGES.flatMap(([k]) => [k, `shift+${k}`]),
    scope: 'canvas',
    when: s => s.graph != null && !s.graph.readOnly && (s.selectedNodeIds.length > 0 || s.selectedAnnotationIds.length > 0),
    run(ctx) {
      const chord = ((ctx.event?.shiftKey ? 'shift+' : '') + (ctx.event?.key ?? '').toLowerCase())
      const big = chord.startsWith('shift+')
      const hit = NUDGES.find(([k]) => chord.endsWith(k))
      if (!hit) return false
      const step = big ? NUDGE_BIG : 1
      return nudge(ctx, hit[1] * step, hit[2] * step)
    },
  },
]
