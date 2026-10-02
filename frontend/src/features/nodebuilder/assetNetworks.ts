/**
 * Where locked asset instances get their children on the canvas (W6, S38).
 *
 * networkNav.ts draws a locked instance's contents from a registered
 * source (`registerAssetNetworkSource`); this module is that source: the
 * asset files graphLibrary.ts caches by `name@version`. Whenever the store
 * graph or the graph on screen holds a locked instance whose file is not
 * cached, it fetches the file and calls `notifyAssetNetworks()` when it
 * lands, so the canvas redraws. Locked instances inside a fetched asset are
 * fetched too (an asset may hold another asset).
 *
 * A failed fetch (asset_missing, offline) is not retried for
 * `RETRY_AFTER_MS`, so a graph that commits often does not hammer the
 * server; the diagnostics already say the asset is missing.
 *
 * Installed once by plugins/assets.ts.
 */

import type { Graph, GraphNode } from '../../api/nodebuilder'
import { cachedAsset, forgetMissingAssets, getAsset } from '../../api/graphLibrary'
import { isLockedInstance, notifyAssetNetworks, registerAssetNetworkSource } from './networkNav'
import { getScreenGraph, subscribeScreenGraph } from './screen'
import { useNodeBuilderStore } from './store'
import { ensureLibrary, useAssetLibrary } from './assetUi'

export const RETRY_AFTER_MS = 30_000

const pending = new Set<string>()
const failedAt = new Map<string, number>()

function keyOf(ref: { name: string; version: number }): string {
  return `${ref.name}@${ref.version}`
}

/** The distinct `{name, version}` of every locked instance in `nodes`. */
export function lockedRefsOf(nodes: Record<string, GraphNode> | null | undefined): { name: string; version: number }[] {
  const out = new Map<string, { name: string; version: number }>()
  for (const n of Object.values(nodes ?? {})) {
    if (!isLockedInstance(n) || !n.asset_ref) continue
    const ref = { name: n.asset_ref.name, version: n.asset_ref.version }
    out.set(keyOf(ref), ref)
  }
  return [...out.values()]
}

/** Fetch the file of every locked instance in `graph` that is not cached yet. */
export function ensureAssetNetworks(graph: Pick<Graph, 'nodes'> | null | undefined, now: number = Date.now()): void {
  if (!graph) return
  // The card's newer-version dot and Rules colour read the library list
  // (UX-04): load it once when a graph holds an asset instance. Later
  // refreshes come from the Asset Manager, the Tab menu and saves.
  if (useAssetLibrary.getState().status === 'idle' && Object.values(graph.nodes).some(n => n.asset_ref)) void ensureLibrary()
  for (const ref of lockedRefsOf(graph.nodes)) {
    const key = keyOf(ref)
    if (pending.has(key) || cachedAsset(ref.name, ref.version)) continue
    const failed = failedAt.get(key)
    if (failed !== undefined && now - failed < RETRY_AFTER_MS) continue
    // Our own retry window has passed: ask the server, not the API's 404 memory.
    if (failed !== undefined) forgetMissingAssets(ref.name, ref.version)
    pending.add(key)
    getAsset(ref.name, ref.version).then(
      file => {
        pending.delete(key)
        failedAt.delete(key)
        notifyAssetNetworks()
        ensureAssetNetworks(file.network)
      },
      () => {
        pending.delete(key)
        failedAt.set(key, Date.now())
      },
    )
  }
}

/** Tests: forget pending and failed fetches. */
export function resetAssetNetworks(): void {
  pending.clear()
  failedAt.clear()
}

/**
 * Register the library as the locked-instance source and fetch for the
 * graphs on screen as they change. Returns the function that undoes it.
 */
export function installAssetNetworks(): () => void {
  const removeSource = registerAssetNetworkSource(ref => cachedAsset(ref.name, ref.version)?.network)
  const unsubStore = useNodeBuilderStore.subscribe((s, prev) => {
    if (s.graph !== prev.graph) ensureAssetNetworks(s.graph)
  })
  const unsubScreen = subscribeScreenGraph(() => ensureAssetNetworks(getScreenGraph().graph))
  ensureAssetNetworks(useNodeBuilderStore.getState().graph)
  ensureAssetNetworks(getScreenGraph().graph)
  return () => {
    removeSource()
    unsubStore()
    unsubScreen()
  }
}
