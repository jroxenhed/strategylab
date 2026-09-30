/**
 * persistence.ts — browser-side persistence for the node builder
 * (plan W1 item 1.F; surfaces S01, S03, S04; storage keys amendment A5).
 *
 * The server is the source of truth for saved graphs. This file keeps only:
 * - drafts: the unsaved edit copy of a graph, in localStorage, so edits
 *   survive a reload or a crash (S03). Never restored without asking.
 * - small per-browser keys (the last open graph, the browser sort, the
 *   one-time legacy seed flag).
 * - pure helpers: graph equality, the S04 structural diff, import parsing,
 *   export payloads, copy names.
 *
 * Every storage call is wrapped in try/catch: a private window or blocked
 * storage must never break the page.
 */

import { useEffect } from 'react'
import type { Graph } from '../../api/nodebuilder'
import { seedLegacyGraphs, type GraphEnvelope } from '../../api/graphs'
import { useNodeBuilderStore } from './store'

// ---------------------------------------------------------------------------
// Keys and safe storage
// ---------------------------------------------------------------------------

export const LEGACY_GRAPHS_KEY = 'strategylab-saved-graphs'
export const LEGACY_MIGRATED_KEY = 'strategylab-saved-graphs.migrated'
export const SEEDED_KEY = 'nb.seeded'
export const LAST_GRAPH_KEY = 'nb.lastGraph'
export const BROWSER_SORT_KEY = 'nb.browser.sort'
export const DRAFT_NEW_KEY = 'nb.draft.new'

/** Draft key for a graph id; untitled graphs share `nb.draft.new`. */
export function draftKey(graphId: string | null | undefined): string {
  return graphId ? `nb.draft.${graphId}` : DRAFT_NEW_KEY
}

export function storageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function storageSet(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

export function storageRemove(key: string): void {
  try {
    window.localStorage.removeItem(key)
  } catch {
    /* storage blocked: nothing to remove */
  }
}

export function getLastGraphId(): string | null {
  return storageGet(LAST_GRAPH_KEY)
}

export function setLastGraphId(id: string | null): void {
  if (id) storageSet(LAST_GRAPH_KEY, id)
  else storageRemove(LAST_GRAPH_KEY)
}

// ---------------------------------------------------------------------------
// Graph equality (content, after JSON normalization)
// ---------------------------------------------------------------------------

/** JSON with object keys sorted, so key order never makes two graphs differ. */
export function stableStringify(value: unknown): string {
  if (value === undefined) return 'null'
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  const obj = value as Record<string, unknown>
  const keys = Object.keys(obj).filter(k => obj[k] !== undefined).sort()
  return `{${keys.map(k => `${JSON.stringify(k)}:${stableStringify(obj[k])}`).join(',')}}`
}

/**
 * A graph as text for content comparison: `readOnly` left out, and the wires
 * as a sorted set. Wire order carries no meaning (the port does), and the
 * server writes each node's inputs in port order, so a saved graph can come
 * back with its wires in another order.
 */
function contentKey(g: Graph): string {
  const wires = (g.wires ?? []).map(stableStringify).sort()
  return stableStringify({ ...g, readOnly: false, wires: [] }) + wires.join('')
}

/** True when two graphs have the same content. `readOnly` and wire order are ignored. */
export function graphsEqual(a: Graph | null | undefined, b: Graph | null | undefined): boolean {
  if (a === b) return true
  if (!a || !b) return false
  return contentKey(a) === contentKey(b)
}

// ---------------------------------------------------------------------------
// Drafts (S03)
// ---------------------------------------------------------------------------

export interface Draft {
  /** Server id, or null for an untitled graph. */
  graphId: string | null
  /** The rev the edits were based on (0 for an untitled graph). */
  rev: number
  name: string
  /** ISO time of the write. */
  savedAt: string
  graph: Graph
}

/** Drafts bigger than this are not written (localStorage is small). */
export const DRAFT_MAX_BYTES = 1024 * 1024
/** A dirty graph is written to its draft at most this often. */
export const DRAFT_INTERVAL_MS = 5000

let loggedTooBig = false

/** Write a draft. Returns false when it was skipped (too big, storage blocked). */
export function saveDraft(draft: Omit<Draft, 'savedAt'> & { savedAt?: string }): boolean {
  const full: Draft = { ...draft, savedAt: draft.savedAt ?? new Date().toISOString() }
  let json: string
  try {
    json = JSON.stringify(full)
  } catch {
    return false
  }
  if (json.length > DRAFT_MAX_BYTES) {
    if (!loggedTooBig) {
      loggedTooBig = true
      console.warn('[nodebuilder] draft is over 1 MB; not saving drafts for this graph')
    }
    return false
  }
  return storageSet(draftKey(full.graphId), json)
}

/** Read a draft, or null when there is none or it does not parse. */
export function readDraft(graphId: string | null | undefined): Draft | null {
  const raw = storageGet(draftKey(graphId))
  if (!raw) return null
  try {
    const d = JSON.parse(raw) as Partial<Draft>
    if (!d || typeof d !== 'object' || !d.graph || typeof d.graph !== 'object') return null
    const graph = d.graph as Graph
    if (!graph.nodes || typeof graph.nodes !== 'object' || !Array.isArray(graph.wires)) return null
    return {
      graphId: typeof d.graphId === 'string' ? d.graphId : null,
      rev: typeof d.rev === 'number' ? d.rev : 0,
      name: typeof d.name === 'string' ? d.name : 'untitled',
      savedAt: typeof d.savedAt === 'string' ? d.savedAt : new Date(0).toISOString(),
      graph,
    }
  } catch {
    return null
  }
}

export function clearDraft(graphId: string | null | undefined): void {
  storageRemove(draftKey(graphId))
}

/** The restore prompt a loaded graph needs, or null (S03 "When the banner shows"). */
export type DraftVariant = 'same' | 'moved'

export function draftPromptFor(
  draft: Draft | null,
  loaded: Graph,
  serverRev: number,
): DraftVariant | null {
  if (!draft) return null
  // Compare content, not rev: a saved-then-reloaded graph must not prompt.
  if (graphsEqual(draft.graph, loaded)) return null
  return draft.rev < serverRev ? 'moved' : 'same'
}

/** Write the store's current graph to its draft key (no-op when clean or read-only). */
export function writeDraftNow(): boolean {
  const s = useNodeBuilderStore.getState()
  if (!s.graph || s.graph.readOnly || !s.dirty) return false
  return saveDraft({
    graphId: s.graphMeta?.id ?? null,
    rev: s.graphMeta?.rev ?? 0,
    name: s.graphMeta?.name ?? 'untitled',
    graph: s.graph,
  })
}

/**
 * Autosave drafts while the graph is dirty: the first dirty commit starts a
 * 5 s timer, the timer writes the draft, the next dirty commit starts a new
 * one. Clean graphs stop the timer. Also writes on tab hide and on unload,
 * and asks before leaving the page with unsaved edits.
 */
export function useDraftAutosave(onWrite?: () => void): void {
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    const stop = () => {
      if (timer != null) clearTimeout(timer)
      timer = null
    }
    const check = () => {
      const s = useNodeBuilderStore.getState()
      const editing = s.graph != null && !s.graph.readOnly && s.dirty
      if (!editing) {
        stop()
        return
      }
      if (timer == null) {
        timer = setTimeout(() => {
          timer = null
          if (writeDraftNow()) onWrite?.()
        }, DRAFT_INTERVAL_MS)
      }
    }
    const unsub = useNodeBuilderStore.subscribe((s, prev) => {
      if (s.commitSeq !== prev.commitSeq || s.dirty !== prev.dirty || s.graph !== prev.graph) check()
    })
    const onHide = () => {
      if (document.visibilityState === 'hidden') writeDraftNow()
    }
    const onUnload = (e: BeforeUnloadEvent) => {
      const s = useNodeBuilderStore.getState()
      if (s.graph && !s.graph.readOnly && s.dirty) {
        writeDraftNow()
        e.preventDefault()
        // Older browsers need returnValue set to show the prompt.
        e.returnValue = ''
      }
    }
    document.addEventListener('visibilitychange', onHide)
    window.addEventListener('beforeunload', onUnload)
    check()
    return () => {
      stop()
      unsub()
      document.removeEventListener('visibilitychange', onHide)
      window.removeEventListener('beforeunload', onUnload)
    }
    // onWrite is a notification only; the effect runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}

// ---------------------------------------------------------------------------
// One-time legacy seed (S01)
// ---------------------------------------------------------------------------

/** How many graphs a legacy localStorage value holds (object map or array). */
export function countLegacyGraphs(raw: string | null): number {
  if (!raw) return 0
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return 0
  }
  const isObj = (v: unknown) => typeof v === 'object' && v !== null && !Array.isArray(v)
  if (Array.isArray(data)) return data.filter(g => isObj(g) && isObj((g as { graph?: unknown }).graph)).length
  if (isObj(data)) return Object.values(data as Record<string, unknown>).filter(isObj).length
  return 0
}

export interface SeedOutcome {
  imported: number
  /** Skipped because the server already has a graph by that name. */
  duplicates: number
  /** Skipped because the server could not read them (reason `invalid`). */
  unreadable: number
  /** The legacy value is over the request size limit (413); nothing was sent. */
  tooLarge?: boolean
}

/**
 * Import the old browser-only saved graphs to the server, once. Returns
 * null when there was nothing to do or the call failed (it retries on the
 * next mount). On success the legacy value is copied to the `.migrated` key
 * and only then removed: if that copy cannot be written (storage full) the
 * legacy key stays, so graphs the server could not read are never lost.
 * A 413 (too large to send) returns `tooLarge` so the page can say so; it is
 * retried on the next load, as nothing was imported.
 */
export async function runLegacySeed(): Promise<SeedOutcome | null> {
  if (storageGet(SEEDED_KEY)) return null
  const raw = storageGet(LEGACY_GRAPHS_KEY)
  if (countLegacyGraphs(raw) === 0) return null
  try {
    const res = await seedLegacyGraphs(raw)
    if (storageSet(LEGACY_MIGRATED_KEY, raw as string)) storageRemove(LEGACY_GRAPHS_KEY)
    storageSet(SEEDED_KEY, '1')
    const duplicates = res.skipped.filter(s => s.reason === 'duplicate').length
    return { imported: res.imported.length, duplicates, unreadable: res.skipped.length - duplicates }
  } catch (e) {
    if ((e as { response?: { status?: number } })?.response?.status === 413) {
      return { imported: 0, duplicates: 0, unreadable: 0, tooLarge: true }
    }
    console.warn('[nodebuilder] could not import legacy saved graphs; will retry', e)
    return null
  }
}

/** S01 seed banner text; the "could not be read" and too-large parts are this pass's addition. */
export function seedBannerText(o: SeedOutcome): string {
  if (o.tooLarge) {
    return 'The saved graphs in this browser are too large to import in one request. They are kept in this browser.'
  }
  const n = o.imported
  let text = `Imported ${n} saved graph${n === 1 ? '' : 's'} from this browser to the server. Open one from ⋯ › Open.`
  if (o.duplicates > 0) text += ` · ${o.duplicates} skipped (duplicates)`
  if (o.unreadable > 0) text += ` · ${o.unreadable} could not be read (kept in this browser)`
  return text
}

// ---------------------------------------------------------------------------
// Names
// ---------------------------------------------------------------------------

export const NAME_MAX = 80

/** The name dialog's error for a name, or null when it is fine (S01). */
export function nameError(name: string): string | null {
  const t = name.trim()
  if (!t) return 'Enter a name.'
  if (t.length > NAME_MAX) return 'Use 80 characters or fewer.'
  return null
}

export function nameTakenText(name: string): string {
  return `A graph named "${name.trim()}" already exists.`
}

/** `<name> copy`, then `<name> copy 2`, `3`, ... (attempt 0 is the first). */
export function copyName(name: string, attempt = 0): string {
  const base = `${name} copy`
  return attempt === 0 ? base : `${base} ${attempt + 1}`
}

/** `<name>`, then `<name> (imported)`, `<name> (imported 2)`, ... */
export function importName(name: string, attempt = 0): string {
  if (attempt === 0) return name
  if (attempt === 1) return `${name} (imported)`
  return `${name} (imported ${attempt})`
}

// ---------------------------------------------------------------------------
// Import and export (S01)
// ---------------------------------------------------------------------------

function looksLikeGraph(v: unknown): v is Graph {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false
  const g = v as { nodes?: unknown; wires?: unknown }
  return !!g.nodes && typeof g.nodes === 'object' && !Array.isArray(g.nodes) && Array.isArray(g.wires)
}

/** File name without `.graph.json` / `.json`, used for a bare graph's name. */
export function baseFileName(fileName: string): string {
  const stripped = fileName.replace(/\.graph\.json$/i, '').replace(/\.json$/i, '')
  return stripped.trim() || 'Imported graph'
}

/**
 * The graphs an imported file holds: a GraphEnvelope, a bare Graph, or a
 * legacy `{name: Graph}` map. Throws an Error with a sentence when it holds
 * none of these.
 */
export function parseImport(text: string, fileName: string): { name: string; graph: Graph }[] {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    throw new Error('the file is not valid JSON')
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('the file does not hold a graph')
  }
  const obj = data as Record<string, unknown>
  if (looksLikeGraph(obj.graph)) {
    const name = typeof obj.name === 'string' && obj.name.trim() ? obj.name.trim() : baseFileName(fileName)
    return [{ name, graph: obj.graph }]
  }
  if (looksLikeGraph(obj)) return [{ name: baseFileName(fileName), graph: obj }]
  const entries = Object.entries(obj).filter(([, g]) => looksLikeGraph(g)) as [string, Graph][]
  if (entries.length === 0) throw new Error('the file does not hold a graph')
  return entries.map(([name, graph]) => ({ name: name.trim() || 'Imported graph', graph }))
}

/** What Export JSON writes: the server envelope with the graph on screen, or `{name, graph}` when untitled. */
export function exportPayload(
  graph: Graph,
  name: string,
  envelope: GraphEnvelope | null,
): Record<string, unknown> {
  if (!envelope) return { name, graph }
  return { ...envelope, graph }
}

export function exportFileName(name: string): string {
  const safe = name.replace(/[\\/:*?"<>|]+/g, '_').trim() || 'graph'
  return `${safe}.graph.json`
}

/** Start a browser download of a JSON value. */
export function downloadJson(fileName: string, value: unknown): void {
  try {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fileName
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (e) {
    console.warn('[nodebuilder] download failed', e)
  }
}

// ---------------------------------------------------------------------------
// Structural diff for the conflict dialog (S04)
// ---------------------------------------------------------------------------

export interface GraphDiff {
  added: string[]
  removed: string[]
  changed: { name: string; what: string[] }[]
  nameChange: [string, string] | null
  /** True when the only differences are node positions. */
  onlyPositions: boolean
  /** True when the graphs have the same content. */
  identical: boolean
}

function nodeLabelOf(g: Graph, id: string): string {
  return g.nodes[id]?.name || id
}

function wireSigs(g: Graph, id: string): string {
  return g.wires
    .filter(w => w.from === id || w.to === id)
    .map(w => `${w.from}>${w.to}:${w.from_port ?? 'out'}:${w.to_port ?? ''}`)
    .sort()
    .join('|')
}

/**
 * Compare the local graph with the server graph, node by node. "Added on
 * the server" means the server has it and the local copy does not.
 */
export function compareGraphs(
  local: Graph,
  server: Graph,
  names?: { local: string; server: string },
): GraphDiff {
  const added = Object.keys(server.nodes).filter(id => !(id in local.nodes)).map(id => nodeLabelOf(server, id))
  const removed = Object.keys(local.nodes).filter(id => !(id in server.nodes)).map(id => nodeLabelOf(local, id))
  const changed: { name: string; what: string[] }[] = []
  let otherThanPosition = added.length > 0 || removed.length > 0
  for (const id of Object.keys(local.nodes)) {
    const a = local.nodes[id]
    const b = server.nodes[id]
    if (!b) continue
    const what: string[] = []
    if (stableStringify(a.params) !== stableStringify(b.params)) what.push('params')
    if (wireSigs(local, id) !== wireSigs(server, id)) what.push('wires')
    if (a.name !== b.name || a.type !== b.type || a.parent !== b.parent) what.push('name')
    if (a.display !== b.display || a.bypass !== b.bypass) what.push('flags')
    const pa = a.position ?? [0, 0]
    const pb = b.position ?? [0, 0]
    if (pa[0] !== pb[0] || pa[1] !== pb[1]) what.push('position')
    if (what.length > 0) {
      changed.push({ name: b.name || id, what })
      if (what.some(w => w !== 'position')) otherThanPosition = true
    }
  }
  const nameChange: [string, string] | null =
    names && names.local !== names.server ? [names.local, names.server] : null
  if (nameChange) otherThanPosition = true
  const rest = { ...local, nodes: {}, wires: [], readOnly: false }
  const restServer = { ...server, nodes: {}, wires: [], readOnly: false }
  const otherFields = stableStringify(rest) !== stableStringify(restServer)
  if (otherFields) otherThanPosition = true
  const identical = !otherThanPosition && changed.length === 0
  return {
    added,
    removed,
    changed,
    nameChange,
    onlyPositions: !otherThanPosition && changed.length > 0,
    identical,
  }
}

/** The diff as the lines the Compare section shows (S04). */
export function describeGraphDiff(d: GraphDiff): string[] {
  if (d.identical) return ['The graphs are the same.']
  if (d.onlyPositions) return ['Only node positions differ.']
  const lines: string[] = []
  const plural = (n: number) => (n === 1 ? 'node' : 'nodes')
  if (d.added.length) lines.push(`+ ${d.added.length} ${plural(d.added.length)} added on the server: ${d.added.join(', ')}`)
  if (d.removed.length) lines.push(`− ${d.removed.length} ${plural(d.removed.length)} removed on the server: ${d.removed.join(', ')}`)
  if (d.changed.length) {
    const parts = d.changed.map(c => `${c.name} (${c.what.join(', ')})`)
    lines.push(`~ ${d.changed.length} ${plural(d.changed.length)} changed: ${parts.join(', ')}`)
  }
  if (d.nameChange) lines.push(`~ name changed: ${d.nameChange[0]} → ${d.nameChange[1]}`)
  if (lines.length === 0) lines.push('~ graph settings changed')
  return lines
}
