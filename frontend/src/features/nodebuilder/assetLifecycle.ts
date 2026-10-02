/**
 * Asset instance lifecycle (S38 states table, UX-04): a newer version
 * exists ("v4 available · Update", "Update to v4"), an unlocked copy goes
 * back to its version ("Re-lock to v3"), the version was deleted
 * (asset_missing text), and a network with no output.
 *
 * The edits are pure functions in operations/assets.ts; this module fetches
 * the asset file, checks the graph again after the fetch, and makes ONE
 * commit, as `unlockAssetInstance` does (commands/assets.ts).
 */

import type { Graph, GraphNode } from '../../api/nodebuilder'
import { assetErrorText, getAsset, type AssetFile, type AssetListItem } from '../../api/graphLibrary'
import { localCopyMatches, relockInstance, updateInstanceVersion } from './operations/assets'
import { insideLockedAsset } from './operations/collapse'
import { useNodeBuilderStore } from './store'

/** Copy (S38). */
export const LIFECYCLE_TEXT = {
  available: (v: number) => `v${v} available`,
  update: 'Update',
  updateTo: (v: number) => `Update to v${v}`,
  relockTo: (v: number) => `Re-lock to v${v}`,
  relockDiffers: (v: number) => `The local copy differs from v${v}; re-locking would lose the changes.`,
  missing: 'This asset version was deleted from the library. The graph cannot cook until you replace it or unlock a local copy (not possible: the definition is gone). Remove the node or restore the asset.',
  noOutput: 'This network has no output. Add a Subnet output inside it.',
} as const

/** The library's newest version of the instance's asset when it is newer than the one it uses, else null. */
export function newerVersionOf(node: Pick<GraphNode, 'asset_ref'> | null | undefined, items: readonly AssetListItem[]): number | null {
  const ref = node?.asset_ref
  if (!ref) return null
  const item = items.find(i => i.name === ref.name)
  return item && item.latest > ref.version ? item.latest : null
}

/** True when the asset has a Rules palette entry (the card's type slot takes the Rules colour). */
export function hasRulesPalette(node: Pick<GraphNode, 'asset_ref'> | null | undefined, items: readonly AssetListItem[]): boolean {
  const ref = node?.asset_ref
  if (!ref) return false
  return items.find(i => i.name === ref.name)?.palette?.category === 'rules'
}

/** Why `id` cannot be updated or re-locked now, or null. */
function editProblem(graph: Graph | null, id: string): string | null {
  if (!graph) return 'No graph'
  if (graph.readOnly) return 'Read-only graph'
  const n = graph.nodes[id]
  if (!n?.asset_ref) return 'Not an asset instance'
  if (n.parent && insideLockedAsset(graph.nodes, n.parent)) return 'Unlock the outer asset first'
  return null
}

/** Why the locked instance `id` cannot move to `version`, or null. */
export function updateProblem(graph: Graph | null, id: string, version: number | null): string | null {
  const p = editProblem(graph, id)
  if (p) return p
  const n = graph!.nodes[id]
  if (n.locked !== true) return 'Only a locked instance follows the library'
  if (version === null || version <= n.asset_ref!.version) return 'Already the newest version'
  return null
}

/** Why the unlocked copy `id` cannot be re-locked (with its file when known), or null. */
export function relockProblem(graph: Graph | null, id: string, file: AssetFile | null): string | null {
  const p = editProblem(graph, id)
  if (p) return p
  const n = graph!.nodes[id]
  if (n.locked === true) return 'Already locked'
  if (file && !localCopyMatches(graph!, id, file)) return LIFECYCLE_TEXT.relockDiffers(n.asset_ref!.version)
  return null
}

async function fileOrFlash(name: string, version: number, what: string): Promise<AssetFile | null> {
  try {
    return await getAsset(name, version)
  } catch (e) {
    useNodeBuilderStore.getState().showFlash(`Could not ${what} ${name}: ${assetErrorText(e)}`)
    return null
  }
}

/** Update the locked instance `id` to `version` (one commit). */
export async function updateAssetInstance(id: string, version: number): Promise<boolean> {
  const store = useNodeBuilderStore
  const before = store.getState().graph
  const ref = before?.nodes[id]?.asset_ref
  if (!ref || updateProblem(before, id, version)) return false
  const file = await fileOrFlash(ref.name, version, 'update')
  if (!file) return false
  // The graph may have changed while the file loaded: check again.
  const s = store.getState()
  const now = s.graph?.nodes[id]
  if (!now || now.asset_ref?.name !== ref.name || now.asset_ref.version !== ref.version || updateProblem(s.graph, id, version)) return false
  try {
    s.commit(`update ${ref.name} to v${version}`, g => updateInstanceVersion(g, id, file))
  } catch (e) {
    s.showFlash(e instanceof Error ? e.message : 'Could not update the asset')
    return false
  }
  return true
}

/** Re-lock the unlocked copy `id` to its version (one commit), only when nothing in it changed. */
export async function relockAssetInstance(id: string): Promise<boolean> {
  const store = useNodeBuilderStore
  const before = store.getState().graph
  const ref = before?.nodes[id]?.asset_ref
  if (!ref || relockProblem(before, id, null)) return false
  const file = await fileOrFlash(ref.name, ref.version, 're-lock')
  if (!file) return false
  const s = store.getState()
  const now = s.graph?.nodes[id]
  if (!now || now.asset_ref?.name !== ref.name || now.asset_ref.version !== ref.version) return false
  const problem = relockProblem(s.graph, id, file)
  if (problem) {
    s.showFlash(problem)
    return false
  }
  s.commit(`re-lock ${ref.name}`, g => relockInstance(g, id))
  return true
}
