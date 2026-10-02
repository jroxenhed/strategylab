/**
 * Network commands (W6 item 6.C, specs S37, S38, FA1, FA7):
 *
 * - `I` or Enter with a network selected: dive into it.
 * - `U`: up one level (the network you left is selected and framed when it
 *   is off screen). `Shift+U`: to the root.
 * - `X` / `Shift+X`: show the selected networks as cards or frames. X only
 *   acts on networks here (a plain node's header-only collapse is not
 *   built), so X and Shift+X do the same thing today.
 *
 * Shift+C (collapse into a subnet) is item 6.D's (commands/collapse.ts).
 * The work lives in networkNav.ts; this module only describes the commands.
 * It is loaded by commands/index.ts. Nothing else may import this module.
 */

import type { Command } from './index'
import { useNodeBuilderStore, type NodeBuilderState } from '../store'
import type { Graph } from '../../../api/nodebuilder'
import { getScreenGraph } from '../screen'
import {
  diveInto,
  goToRoot,
  goUp,
  isNetworkNode,
  screenNetworkId,
  selectedNetworks,
  toggleSelectedNetworkViews,
  toggleViewLabel,
} from '../networkNav'

/** The graph on screen (the read-only view is not in the store). */
function shown(s: NodeBuilderState): Graph | null {
  return getScreenGraph().graph ?? s.graph
}

/** The network a dive would enter: the primary selection, or the first selected network. */
function diveTarget(s: NodeBuilderState): string | null {
  const g = shown(s)
  if (!g) return null
  if (s.selectedNodeId && isNetworkNode(g.nodes[s.selectedNodeId])) return s.selectedNodeId
  return selectedNetworks(s, g)[0] ?? null
}

/**
 * True when Enter was pressed on a control that acts on Enter itself (a
 * button, link, menu row, crumb...). Then Enter is that control's, not a
 * dive (FE-03): the canvas listener sees every key inside the builder.
 */
function enterOwnedByControl(event: KeyboardEvent | null): boolean {
  if (!event || event.key !== 'Enter') return false
  const t = event.target
  if (!(t instanceof Element)) return false
  return t.closest(
    'button, a[href], summary, [role="button"], [role="link"], [role="menuitem"], [role="option"], [role="tab"], [role="checkbox"], [role="radio"], [role="switch"]',
  ) !== null
}

const inside = (s: NodeBuilderState) => screenNetworkId(shown(s), s) !== null

export const commands: Command[] = [
  {
    id: 'network.dive',
    label: 'Dive into network',
    keys: ['i', 'enter'],
    // The keys also work in the read-only view (S37: "crumbs work"); the
    // node menu row shows only on an editable graph, so the read-only menu
    // keeps its S19 rows (Copy, Frame, Show data).
    get menu() { return getScreenGraph().editable ? 'node' as const : undefined },
    readOnlyOk: true,
    when: s => diveTarget(s) !== null,
    disabledReason: s => (diveTarget(s) !== null ? null : 'Select a network first'),
    run({ store, canvas, event }) {
      if (enterOwnedByControl(event)) return false
      const id = diveTarget(store.getState())
      if (!id) return false
      return diveInto(id, canvas)
    },
  },
  {
    id: 'network.up',
    label: 'Go up',
    keys: ['u'],
    // S37: at the root the pane menu has no `Go up` row at all.
    get menu() { return inside(useNodeBuilderStore.getState()) ? 'pane' as const : undefined },
    readOnlyOk: true,
    when: inside,
    disabledReason: s => (inside(s) ? null : 'Already at the root'),
    run({ canvas }) {
      return goUp(canvas)
    },
  },
  {
    id: 'network.root',
    label: 'Go to root',
    keys: ['shift+u'],
    readOnlyOk: true,
    when: inside,
    run({ canvas }) {
      return goToRoot(canvas)
    },
  },
  {
    id: 'network.toggleView',
    // The menu row reads `Show as card` or `Show as frame` for the selection.
    get label() { return toggleViewLabel() },
    keys: ['x', 'shift+x'],
    menu: 'node',
    when: s => !!s.graph && !s.graph.readOnly && selectedNetworks(s, s.graph).length > 0,
    disabledReason: s => (selectedNetworks(s, s.graph).length > 0 ? null : 'Select a network first'),
    run({ canvas }) {
      return toggleSelectedNetworkViews(canvas)
    },
  },
]
