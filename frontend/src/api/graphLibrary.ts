/**
 * Asset library API (plan W6 contract, F435 item 6.D).
 *
 * An asset is a saved sub-network: a subnet's children and wires, its
 * promoted params, its declared interface and an optional Tab-menu entry.
 * Assets are immutable per version: a save with a name that exists makes
 * the next version, and old versions never change. Graphs place an asset
 * as a locked `subnet` instance that pins `{name, version}`.
 *
 *   GET    /api/graph_library                   -> {assets: AssetListItem[]}
 *   GET    /api/graph_library/{name}/{version}  -> AssetFile | 404
 *   POST   /api/graph_library                   -> 201 AssetFile (version = latest + 1)
 *   DELETE /api/graph_library/{name}/{version}  -> 204
 *
 * A full asset file is fetched only for the version that is needed, and
 * kept in a cache by `name@version` (it can never change on the server).
 */

import axios from 'axios'
import { api } from './client'
import type { GraphNode, GraphWire } from './nodebuilder'
import type { ParamType } from '../features/nodebuilder/catalog.generated'

export interface AttrDecl { name: string; class: 'point' | 'detail'; dtype: string }
export interface PromotedParam { name: string; label: string; target: string; type: ParamType; default: unknown }
export interface AssetFile {
  name: string; version: number; description: string; stream_schema: number
  interface: { reads: AttrDecl[]; writes: AttrDecl[] }
  promoted: PromotedParam[]
  palette: { category: 'rules'; label: string; glyph: string } | null
  network: { nodes: Record<string, GraphNode>; wires: GraphWire[] }
  created_at: string
}
export interface AssetListItem { name: string; versions: number[]; latest: number; palette: AssetFile['palette']; interface: AssetFile['interface']; used_by: { graph_id: string; name: string }[] }

/** The POST body. `interface` and `palette` may be left out. */
export interface SaveAssetBody {
  name: string
  description: string
  network: AssetFile['network']
  promoted: PromotedParam[]
  interface?: AssetFile['interface']
  palette?: AssetFile['palette']
}

/** Asset names: the same rule as node names. */
export const ASSET_NAME_RE = /^[a-z_][a-z0-9_]{0,63}$/

// Full files by `name@version`. Versions are immutable, so a cached file
// never goes stale; only a delete drops it.
const fileCache = new Map<string, AssetFile>()
const inFlight = new Map<string, Promise<AssetFile>>()
// Bumped by a delete: a read that started before it never caches its
// answer, so a deleted version cannot come back (FE-10).
const generation = new Map<string, number>()
// Versions the server answered 404 for, with when and the error, so render
// code asking again does not send a request each time (FE-10). Cleared by
// `forgetMissingAssets` (a library refresh) and by a save of that version.
const missing = new Map<string, { at: number; error: unknown }>()
export const MISSING_TTL_MS = 30_000

function cacheKey(name: string, version: number): string {
  return `${name}@${version}`
}

/** Every asset with its versions, palette entry, interface and users. */
export async function listAssets(signal?: AbortSignal): Promise<AssetListItem[]> {
  const { data } = await api.get<{ assets: AssetListItem[] }>('/api/graph_library', { signal })
  return Array.isArray(data?.assets) ? data.assets : []
}

/** One version's full file (cached; a second call for the same version sends nothing). */
export function getAsset(name: string, version: number): Promise<AssetFile> {
  const key = cacheKey(name, version)
  const hit = fileCache.get(key)
  if (hit) return Promise.resolve(hit)
  const pending = inFlight.get(key)
  if (pending) return pending
  const gone = missing.get(key)
  if (gone && Date.now() - gone.at < MISSING_TTL_MS) return Promise.reject(gone.error)
  const gen = generation.get(key) ?? 0
  const p: Promise<AssetFile> = api
    .get<AssetFile>(`/api/graph_library/${encodeURIComponent(name)}/${version}`)
    .then(
      ({ data }) => {
        if ((generation.get(key) ?? 0) === gen) {
          fileCache.set(key, data)
          missing.delete(key)
        }
        return data
      },
      (e: unknown) => {
        if (axios.isAxiosError(e) && e.response?.status === 404) missing.set(key, { at: Date.now(), error: e })
        throw e
      },
    )
    .finally(() => { if (inFlight.get(key) === p) inFlight.delete(key) })
  inFlight.set(key, p)
  return p
}

/**
 * Forget the remembered 404s (a library refresh asks the server again), or
 * only the one for `name@version` (a caller with its own retry policy).
 */
export function forgetMissingAssets(name?: string, version?: number): void {
  if (name !== undefined && version !== undefined) missing.delete(cacheKey(name, version))
  else missing.clear()
}

/** A version's file if it is already cached, else null (for render code). */
export function cachedAsset(name: string, version: number): AssetFile | null {
  return fileCache.get(cacheKey(name, version)) ?? null
}

/** Save a new version. The reply is the stored file (with its version). */
export async function saveAsset(body: SaveAssetBody): Promise<AssetFile> {
  const { data } = await api.post<AssetFile>('/api/graph_library', body)
  if (data && typeof data.name === 'string' && typeof data.version === 'number') {
    fileCache.set(cacheKey(data.name, data.version), data)
    missing.delete(cacheKey(data.name, data.version))
  }
  return data
}

/** Delete one version. Graphs that use it then show `asset_missing`; bots keep their copy. */
export async function deleteAssetVersion(name: string, version: number): Promise<void> {
  await api.delete(`/api/graph_library/${encodeURIComponent(name)}/${version}`)
  const key = cacheKey(name, version)
  // A read still in flight must not put the deleted file back (FE-10).
  generation.set(key, (generation.get(key) ?? 0) + 1)
  inFlight.delete(key)
  fileCache.delete(key)
}

/** Test helper: forget every cached file. */
export function clearAssetCache(): void {
  fileCache.clear()
  inFlight.clear()
  missing.clear()
}

/**
 * The sentence to show for a failed library call: the server's
 * `detail.message` or `detail` string, never the axios text.
 */
export function assetErrorText(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail) return detail
    if (detail && typeof detail === 'object') {
      const msg = (detail as { message?: unknown }).message
      if (typeof msg === 'string' && msg) return msg
    }
    if (e.response) return `The library answered ${e.response.status}.`
    return 'Could not reach the library.'
  }
  return e instanceof Error && e.message ? e.message : 'Could not reach the library.'
}
