/**
 * Tab-menu rows for library assets (spec S43, W6 item 6.D). Assets join the
 * menu at runtime, shaped like catalog entries so search and the category
 * lists treat them the same. Kept apart from TabMenu.tsx so that file only
 * exports components (fast refresh).
 */

import { cachedAsset, type AssetListItem } from '../../api/graphLibrary'
import type { NodeCatalogEntry } from './catalog'
import { CATS, type CatKey } from './categories'
import { assetRowDesc } from './assetText'
import { friendlyName } from './search'

/** The Library category key (assets only; not a node category). */
export const LIBRARY_CAT = 'library'

/** Asset rows carry this prefix in `name`, so they never clash with a node type. */
export const ASSET_ROW_PREFIX = 'asset:'

/** A menu row for a library asset (shaped like a catalog entry for search and lists). */
export interface AssetRowEntry extends NodeCatalogEntry {
  asset: AssetListItem
  label: string
  glyph: string
}

export function isAssetRow(e: NodeCatalogEntry): e is AssetRowEntry {
  return e.name.startsWith(ASSET_ROW_PREFIX)
}

/** The asset's description: the cached latest file, else a list field when the server sends one. */
function assetDescription(a: AssetListItem): string {
  const file = cachedAsset(a.name, a.latest)
  if (file) return file.description
  const d = (a as AssetListItem & { description?: unknown }).description
  return typeof d === 'string' ? d : ''
}

/** One asset as a menu row. */
export function assetRowOf(a: AssetListItem): AssetRowEntry {
  const desc = assetRowDesc(a.latest, assetDescription(a))
  return {
    name: `${ASSET_ROW_PREFIX}${a.name}`,
    cat: a.palette ? 'rules' : LIBRARY_CAT,
    desc,
    reads: a.interface.reads.map(r => r.name),
    writes: a.interface.writes.map(w => w.name),
    defaults: { params: {}, ins: 0, outs: 0, subtitle: desc },
    compileActive: true,
    asset: a,
    label: a.palette?.label || friendlyName(a.name),
    glyph: a.palette?.glyph || 'N',
  }
}

/** The text a row shows. */
export function rowLabel(e: NodeCatalogEntry): string {
  return isAssetRow(e) ? e.label : friendlyName(e.name)
}

/** The colour and glyph of a row's pill. */
export function pillOf(e: NodeCatalogEntry): { color: string; glyph: string } {
  if (isAssetRow(e)) {
    return { color: e.asset.palette ? CATS.rules.color : CATS.network.color, glyph: e.glyph }
  }
  const c = CATS[e.cat as CatKey] ?? CATS.indicator
  return { color: c.color, glyph: c.glyph }
}

/** The pill of a category in the left column. */
export function catPill(cat: string): { color: string; glyph: string } {
  if (cat === LIBRARY_CAT) return { color: CATS.network.color, glyph: 'N' }
  const c = CATS[cat as CatKey] ?? CATS.indicator
  return { color: c.color, glyph: c.glyph }
}

