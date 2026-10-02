/**
 * Copy and small pure helpers for the asset dialogs (specs S41, S42, S43),
 * kept out of the component files so fast refresh keeps working and tests
 * can check the exact strings.
 */

import type { AssetFile, AssetListItem, SaveAssetBody } from '../../api/graphLibrary'

// ── Save as asset (S41) ───────────────────────────────────────────────────

export const SAVE_ASSET_TEXT = {
  newAsset: 'New asset',
  libraryUnreachable: 'Could not check the library.',
  noOutput: 'This network has no output. It can be saved, but it will write nothing.',
  nameInvalid: 'Use a-z, 0-9 and _, starting with a letter or _ (at most 64).',
  replace: 'Replace this node with a locked instance of the saved version',
  replaceHelp: 'Off keeps this node as a local copy that does not follow the library.',
  paletteToggle: 'Show in the Tab menu under Rules',
} as const

export function savesAsVersionText(version: number, name: string): string {
  return `Saves as version ${version} of ${name}`
}

export function fixErrorsText(name: string): string {
  return `Fix the errors inside ${name} before saving it as an asset.`
}

export function savedToastText(name: string, version: number): string {
  return `Saved ${name} v${version} to the library.`
}

/** At most two characters (a glyph like `Σ` counts as one). */
export function clampGlyph(text: string): string {
  return Array.from(text).slice(0, 2).join('')
}

/** The body to POST, from the dialog's fields. */
export function buildSaveBody(input: {
  name: string
  description: string
  network: AssetFile['network']
  promoted: AssetFile['promoted']
  iface: AssetFile['interface']
  palette: { label: string; glyph: string } | null
}): SaveAssetBody {
  return {
    name: input.name,
    description: input.description,
    network: input.network,
    promoted: input.promoted,
    interface: input.iface,
    palette: input.palette ? { category: 'rules', label: input.palette.label, glyph: input.palette.glyph } : null,
  }
}

// ── Asset Manager (S42) ───────────────────────────────────────────────────

export const MANAGER_TEXT = {
  title: 'Asset Manager',
  search: 'Search assets or @attributes',
  empty: 'No assets yet.',
  emptyHelp: 'Select nodes, press Shift+C to make a subnet, then right-click it and choose Save as asset…',
  notUsed: 'Not used in any graph.',
  botsFootnote: 'Bots keep their own copy; library changes never affect a running bot.',
  noPreview: 'Preview not available',
  insert: 'Insert into graph',
  insideLocked: 'You are inside a locked asset',
  loading: 'Loading this version…',
} as const

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** The second line of a list row: `v3 · 2 versions · used in 3 graphs`. */
export function managerSubline(a: Pick<AssetListItem, 'latest' | 'versions' | 'used_by'>): string {
  const graphs = new Set(a.used_by.map(u => u.graph_id)).size
  return `v${a.latest} · ${plural(a.versions.length, 'version', 'versions')} · used in ${plural(graphs, 'graph', 'graphs')}`
}

/** The info line above the description when an older version is shown. */
export function olderVersionText(version: number, latest: number): string {
  return `v${version} · a newer version v${latest} exists.`
}

/** Graph names that use an asset, each once. */
export function userGraphNames(a: Pick<AssetListItem, 'used_by'>): string[] {
  const out: string[] = []
  for (const u of a.used_by) if (!out.includes(u.name)) out.push(u.name)
  return out
}

/** The delete confirm sentence (S42 states table). */
export function deleteConfirmText(name: string, version: number | 'all', users: readonly string[]): string {
  const what = version === 'all' ? `all versions of ${name}` : `${name} v${version}`
  if (users.length === 0) return `Delete ${what}? This cannot be undone.`
  const n = users.length
  return `Delete ${what}? ${plural(n, 'graph uses', 'graphs use')} it: ${users.join(', ')}. They will show an "asset missing" error until you replace the node or restore the asset. Bots are not affected.`
}

/** Assets that match a search: `@attr` by interface only, else name, label or interface. */
export function filterAssets(items: readonly AssetListItem[], query: string): AssetListItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return [...items]
  if (q.startsWith('@')) {
    return items.filter(a => [...a.interface.reads, ...a.interface.writes].some(d => d.name.toLowerCase() === q))
  }
  return items.filter(a =>
    a.name.toLowerCase().includes(q)
    || (a.palette?.label ?? '').toLowerCase().includes(q)
    || [...a.interface.reads, ...a.interface.writes].some(d => d.name.toLowerCase() === `@${q}`),
  )
}

export type ManagerSort = 'name' | 'used'

export function sortAssets(items: readonly AssetListItem[], sort: ManagerSort): AssetListItem[] {
  const out = [...items]
  if (sort === 'used') out.sort((a, b) => b.used_by.length - a.used_by.length || a.name.localeCompare(b.name))
  else out.sort((a, b) => a.name.localeCompare(b.name))
  return out
}

// ── Tab menu (S43) ────────────────────────────────────────────────────────

/** An asset row's description: `asset v3 · SPY above its 50-day SMA`. */
export function assetRowDesc(version: number, description: string): string {
  return description ? `asset v${version} · ${description}` : `asset v${version}`
}

export const TAB_MENU_TEXT = {
  manage: 'Manage assets…',
  loading: 'loading assets…',
  unavailable: 'Library unavailable',
  lockedNote: 'Locked asset. Unlock to add nodes.',
} as const

/** The empty-search line (S43). */
export function noMatchText(query: string): string {
  return `No nodes match "${query}". Try a type, a description, or @attribute.`
}
