/**
 * Asset and promotion commands (specs S40, S41, S42; plan W6 item 6.D).
 *
 * Param-row menu (S40), in the `param` slot:
 * - `assets.promote` "Promote to parent…" opens the Promote popover.
 * - `assets.unpromote` "Unpromote" and `assets.goToPromoted` "Go to
 *   promoted parameter" show only on a promoted child row (their `menu`
 *   is read when the menu opens, from the row the menu is about).
 *
 * Node menu (S41, S42), one subnet selected: "Save as asset…",
 * "Promote to palette…", "Open in Asset Manager".
 *
 * Global: `assets.manager` opens the Asset Manager (FA7). It has no key
 * for now (FE-13: Cmd+Shift+A is Chrome's tab search on macOS).
 *
 * `assets.update` "Update to vN" and `assets.relock` "Re-lock to vN" (S38
 * card menu, UX-04) show on one selected asset instance when they apply;
 * the work is in assetLifecycle.ts.
 *
 * `assets.unlock` "Unlock to local copy" (S38): the locked bar inside a
 * locked instance runs it, and the node menu shows it on a selected locked
 * instance. One commit (operations/assets.ts `unlockInstance`).
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { Graph } from '../../../api/nodebuilder'
import { assetErrorText, cachedAsset, getAsset, type AssetFile } from '../../../api/graphLibrary'
import { goUpAndSelect, openAssetManager, openPromote, openSaveAsset, useAssetLibrary } from '../assetUi'
import { getMenuTarget } from '../contextMenuModel'
import { insideLockedAsset } from '../operations/collapse'
import { unlockInstance } from '../operations/assets'
import { isLockedInstance } from '../networkNav'
import { currentParentId } from '../store/view'
import { PROMOTE_TEXT, promoteProblem, promotionOf, unpromoteParam } from '../operations/promote'
import { useNodeBuilderStore, type NodeBuilderState } from '../store'
import type { Command, CommandMenu } from './index'
import {
  LIFECYCLE_TEXT,
  newerVersionOf,
  relockAssetInstance,
  relockProblem,
  updateAssetInstance,
  updateProblem,
} from '../assetLifecycle'

/** The one selected node when it is an asset instance (locked or not). */
function oneInstance(s: NodeBuilderState): string | null {
  const g = s.graph
  if (!g || s.selectedNodeIds.length !== 1) return null
  const id = s.selectedNodeIds[0]
  return g.nodes[id]?.asset_ref ? id : null
}

/** The selected locked instance and the newer version it can move to. */
function updateTarget(s: NodeBuilderState): { id: string; version: number } | null {
  const id = oneInstance(s)
  if (!id || s.graph!.nodes[id].locked !== true) return null
  const version = newerVersionOf(s.graph!.nodes[id], useAssetLibrary.getState().items)
  return version === null ? null : { id, version }
}

/** The selected unlocked copy. */
function relockTarget(s: NodeBuilderState): string | null {
  const id = oneInstance(s)
  return id && s.graph!.nodes[id].locked !== true ? id : null
}

function relockReason(s: NodeBuilderState): string | null {
  const id = relockTarget(s)
  if (!id) return 'Not an unlocked asset copy'
  const ref = s.graph!.nodes[id].asset_ref!
  return relockProblem(s.graph, id, cachedAsset(ref.name, ref.version))
}

/** The one selected node, when it is a subnet. */
function oneSubnet(s: NodeBuilderState): string | null {
  const g = s.graph
  if (!g || s.selectedNodeIds.length !== 1) return null
  const id = s.selectedNodeIds[0]
  return g.nodes[id]?.type === 'subnet' ? id : null
}

function saveProblem(s: NodeBuilderState): string | null {
  const id = oneSubnet(s)
  if (!id) return 'Select one subnet'
  if (s.graph!.readOnly) return 'Read-only graph'
  return null
}

/** The promotion behind the param row the menu is about, or null. */
function menuPromotion(g: Graph | null) {
  const t = getMenuTarget()
  if (!t || !g) return null
  return promotionOf(g, t.nodeId, t.param)
}

/**
 * `menu` read at menu-build time (contextMenuModel reads `cmd.menu` each
 * time it builds a menu): the row exists only on a promoted child row.
 */
function onPromotedRow(): CommandMenu | undefined {
  return menuPromotion(useNodeBuilderStore.getState().graph) ? 'param' : undefined
}

/**
 * The locked instance to unlock: the one selected node when it is a
 * locked instance (node menu), else the network on screen when it is one
 * (the locked bar). Null otherwise.
 */
export function unlockTarget(s: NodeBuilderState): string | null {
  const g = s.graph
  if (!g) return null
  if (s.selectedNodeIds.length === 1 && isLockedInstance(g.nodes[s.selectedNodeIds[0]])) return s.selectedNodeIds[0]
  const here = currentParentId(s, g)
  return here && isLockedInstance(g.nodes[here]) ? here : null
}

function unlockProblem(s: NodeBuilderState): string | null {
  if (!s.graph) return 'No graph'
  if (s.graph.readOnly) return 'Read-only graph'
  const id = unlockTarget(s)
  if (!id) return 'Not a locked asset instance'
  // A locked instance inside another locked asset is the library's copy.
  const parent = s.graph.nodes[id].parent
  if (parent && insideLockedAsset(s.graph.nodes, parent)) return 'Unlock the outer asset first'
  return null
}

/** Unlock `id` into a local copy (one commit), fetching the asset file first when needed. */
export async function unlockAssetInstance(id: string): Promise<boolean> {
  const store = useNodeBuilderStore
  const ref = store.getState().graph?.nodes[id]?.asset_ref
  if (!ref) return false
  let file: AssetFile
  try {
    file = await getAsset(ref.name, ref.version)
  } catch (e) {
    store.getState().showFlash(`Could not unlock ${ref.name} v${ref.version}: ${assetErrorText(e)}`)
    return false
  }
  // The graph may have changed while the file loaded: check again.
  const s = store.getState()
  const node = s.graph?.nodes[id]
  if (!s.graph || s.graph.readOnly || !isLockedInstance(node) || node!.asset_ref?.name !== ref.name || node!.asset_ref?.version !== ref.version) {
    return false
  }
  try {
    s.commit('unlock asset', g => unlockInstance(g, id, file))
  } catch (e) {
    s.showFlash(e instanceof Error ? e.message : 'Could not unlock the asset')
    return false
  }
  return true
}

export const commands: Command[] = [
  {
    id: 'assets.promote',
    label: PROMOTE_TEXT.menu,
    menu: 'param',
    menuSlot: 'param',
    when: s => {
      const t = getMenuTarget()
      return !!t && promoteProblem(s.graph, t.nodeId, t.param) === null
    },
    disabledReason: s => {
      const t = getMenuTarget()
      return t ? promoteProblem(s.graph, t.nodeId, t.param) : 'No param'
    },
    run: ({ store }) => {
      const t = getMenuTarget()
      if (!t || promoteProblem(store.getState().graph, t.nodeId, t.param)) return false
      openPromote({ nodeId: t.nodeId, param: t.param })
    },
  },
  {
    id: 'assets.unpromote',
    label: PROMOTE_TEXT.unpromote,
    get menu() { return onPromotedRow() },
    menuSlot: 'param',
    when: s => {
      const p = menuPromotion(s.graph)
      return !!p && !s.graph!.readOnly && !insideLockedAsset(s.graph!.nodes, p.networkId)
    },
    disabledReason: s => (menuPromotion(s.graph) ? null : 'Not promoted'),
    run: ({ store }) => {
      const s = store.getState()
      const p = menuPromotion(s.graph)
      if (!p) return false
      s.commit('unpromote param', g => unpromoteParam(g, p.networkId, p.entry.name))
    },
  },
  {
    id: 'assets.goToPromoted',
    label: PROMOTE_TEXT.goToPromoted,
    get menu() { return onPromotedRow() },
    menuSlot: 'param',
    readOnlyOk: true,
    when: s => !!menuPromotion(s.graph),
    run: ({ store }) => {
      const s = store.getState()
      const p = menuPromotion(s.graph)
      if (!p) return false
      goUpAndSelect(s, p.networkId)
    },
  },
  {
    id: 'assets.saveAsAsset',
    label: 'Save as asset…',
    menu: 'node',
    when: s => saveProblem(s) === null,
    disabledReason: saveProblem,
    run: ({ store }) => {
      const id = oneSubnet(store.getState())
      if (!id) return false
      openSaveAsset({ subnetId: id, palette: false })
    },
  },
  {
    id: 'assets.promoteToPalette',
    label: 'Promote to palette…',
    menu: 'node',
    when: s => saveProblem(s) === null,
    disabledReason: saveProblem,
    run: ({ store }) => {
      const id = oneSubnet(store.getState())
      if (!id) return false
      openSaveAsset({ subnetId: id, palette: true })
    },
  },
  {
    id: 'assets.openInManager',
    label: 'Open in Asset Manager',
    menu: 'node',
    when: s => {
      const id = oneSubnet(s)
      return !!id && !!s.graph!.nodes[id].asset_ref
    },
    disabledReason: () => 'Not an asset instance',
    run: ({ store }) => {
      const s = store.getState()
      const id = oneSubnet(s)
      const ref = id ? s.graph!.nodes[id].asset_ref : null
      if (!ref) return false
      openAssetManager({ select: ref.name })
    },
  },
  {
    id: 'assets.unlock',
    label: 'Unlock to local copy',
    // Only on a selected locked instance (the locked bar runs it by id).
    get menu() {
      const s = useNodeBuilderStore.getState()
      const g = s.graph
      return s.selectedNodeIds.length === 1 && g && isLockedInstance(g.nodes[s.selectedNodeIds[0]]) ? 'node' : undefined
    },
    when: s => unlockProblem(s) === null,
    disabledReason: unlockProblem,
    run: ({ store }) => {
      const id = unlockTarget(store.getState())
      if (!id) return false
      void unlockAssetInstance(id)
    },
  },
  {
    id: 'assets.update',
    get label() {
      const t = updateTarget(useNodeBuilderStore.getState())
      return t ? LIFECYCLE_TEXT.updateTo(t.version) : 'Update to the newest version'
    },
    // Only on a selected locked instance with a newer version in the library.
    get menu() { return updateTarget(useNodeBuilderStore.getState()) ? 'node' as const : undefined },
    when: s => {
      const t = updateTarget(s)
      return !!t && updateProblem(s.graph, t.id, t.version) === null
    },
    disabledReason: s => {
      const t = updateTarget(s)
      return t ? updateProblem(s.graph, t.id, t.version) : 'No newer version'
    },
    run: ({ store }) => {
      const t = updateTarget(store.getState())
      if (!t) return false
      void updateAssetInstance(t.id, t.version)
    },
  },
  {
    id: 'assets.relock',
    get label() {
      const s = useNodeBuilderStore.getState()
      const id = relockTarget(s)
      return id ? LIFECYCLE_TEXT.relockTo(s.graph!.nodes[id].asset_ref!.version) : 'Re-lock'
    },
    // Only on a selected unlocked copy (asset_ref present, locked false).
    get menu() { return relockTarget(useNodeBuilderStore.getState()) ? 'node' as const : undefined },
    when: s => relockReason(s) === null,
    disabledReason: relockReason,
    run: ({ store }) => {
      const id = relockTarget(store.getState())
      if (!id) return false
      void relockAssetInstance(id)
    },
  },
  {
    id: 'assets.manager',
    label: 'Asset Manager…',
    // No key (FE-13): Cmd+Shift+A is Chrome's tab search on macOS. The
    // toolbar ⋯ menu and the Tab menu footer open it; the UI pass picks a key.
    scope: 'global',
    readOnlyOk: true,
    run: ({ canvas }) => {
      const at = canvas && canvas.pointerOnCanvas() ? canvas.pointer() : null
      openAssetManager({ insertAt: at })
    },
  },
]
