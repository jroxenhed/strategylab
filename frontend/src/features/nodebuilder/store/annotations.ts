/**
 * Annotations slice of the node builder store: network boxes and sticky
 * notes (item 3.E, specs S17 and S18).
 *
 * Boxes and notes live inside the graph (`graph.annotations`), so every
 * action here edits them through `get().commit(label, recipe)`. That makes
 * each change one undo step, and undo, redo and save cover them with no
 * extra work. The backend stores them and never evaluates them.
 *
 * Box membership (S17): a node belongs to the smallest box that contains
 * its center. Membership is only worked out when a drag or a resize stops,
 * never while dragging. Moving a box moves its members (plugins/boxDrag.ts).
 *
 * The pure helpers below are exported for the plugin, the commands and the
 * tests. Only `editingAnnotationId` is not graph data: it says which box
 * label or note text is being edited right now (not history).
 */

import type { StateCreator } from 'zustand'
import type { Graph, NetworkBox, StickyNote } from '../../../api/nodebuilder'
import type { NodeBuilderState } from '../store'
import type { CommitOptions } from './graph'
import { DEFAULT_NODE_SIZE, nodeCenter, rectContains, type RectTuple, type SizeOf, type XY } from '../geometry'
import { registerGraphReconciler } from './reconcile'
import { currentParentId } from './view'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Box tints (S17). The stored `color` is the key; unknown values draw as `network`. */
export const BOX_TINTS = ['network', 'ticker', 'indicator', 'comparison', 'logic', 'rules', 'code', 'neutral'] as const
export type BoxTint = typeof BOX_TINTS[number]

/** Note colors (S18). Unknown values draw as `amber`. */
export const NOTE_COLORS = ['amber', 'blue', 'green', 'grey'] as const
export type NoteColor = typeof NOTE_COLORS[number]

export const BOX_PADDING = 24
export const BOX_DEFAULT_SIZE = { w: 320, h: 200 }
export const BOX_MIN_SIZE = { w: 160, h: 96 }
export const BOX_LABEL_MAX = 40
export const NOTE_DEFAULT_SIZE = { w: 200, h: 88 }
export const NOTE_MIN_SIZE = { w: 120, h: 48 }
export const NOTE_MAX_SIZE = { w: 480, h: 400 }
export const NOTE_TEXT_MAX = 2000

/** The tint a stored box color draws with (`blue`, the plan's example, and unknown values are `network`). */
export function boxTint(color: string | null | undefined): BoxTint {
  return (BOX_TINTS as readonly string[]).includes(color ?? '') ? (color as BoxTint) : 'network'
}

/** The CSS color for a box tint. */
export function boxTintVar(color: string | null | undefined): string {
  const t = boxTint(color)
  return t === 'neutral' ? 'var(--nb-text-muted)' : `var(--nb-cat-${t})`
}

/** The color set a stored note color draws with. */
export function noteColor(color: string | null | undefined): NoteColor {
  return (NOTE_COLORS as readonly string[]).includes(color ?? '') ? (color as NoteColor) : 'amber'
}

// ---------------------------------------------------------------------------
// Geometry helpers (pure)
// ---------------------------------------------------------------------------

// Sizes and rect helpers live in the leaf geometry.ts (EA-13); re-exported
// here for the callers that already import them from this slice.
export { rectContains, nodeCenter, rfSizeOf } from '../geometry'
export type { RectTuple, XY, Size, SizeOf, NodeLookup } from '../geometry'

type Annotations = Graph['annotations']

/** The graph's annotations, empty when an old graph has none. */
export function annotationsOf(graph: Pick<Graph, 'annotations'>): Annotations {
  return graph.annotations ?? { boxes: [], notes: [] }
}


/**
 * The smallest box whose rect contains the point (S17: overlapping boxes
 * are allowed and a node joins the smallest). Boxes in `skip` are ignored.
 */
export function smallestBoxAt(
  boxes: readonly NetworkBox[],
  p: XY,
  skip?: ReadonlySet<string>,
): NetworkBox | null {
  let best: NetworkBox | null = null
  let bestArea = Infinity
  for (const b of boxes) {
    if (skip?.has(b.id)) continue
    if (!rectContains(b.rect, p)) continue
    const area = b.rect[2] * b.rect[3]
    if (area < bestArea) {
      best = b
      bestArea = area
    }
  }
  return best
}

/** Bounding rect of these graph nodes plus padding, or null when none exist. */
export function boundsOfNodes(
  graph: Pick<Graph, 'nodes'>,
  nodeIds: readonly string[],
  sizeOf: SizeOf,
  padding = BOX_PADDING,
): RectTuple | null {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const id of nodeIds) {
    const n = graph.nodes[id]
    if (!n) continue
    const s = sizeOf(id) ?? DEFAULT_NODE_SIZE
    minX = Math.min(minX, n.position[0])
    minY = Math.min(minY, n.position[1])
    maxX = Math.max(maxX, n.position[0] + s.w)
    maxY = Math.max(maxY, n.position[1] + s.h)
  }
  if (!Number.isFinite(minX)) return null
  return [minX - padding, minY - padding, maxX - minX + 2 * padding, maxY - minY + 2 * padding]
}

/** A new annotation id (`box1`, `note2`, ...) that no node, box or note uses. */
export function newAnnotationId(graph: Pick<Graph, 'nodes' | 'annotations'>, prefix: 'box' | 'note'): string {
  const { boxes, notes } = annotationsOf(graph)
  const taken = new Set<string>([...Object.keys(graph.nodes), ...boxes.map(b => b.id), ...notes.map(n => n.id)])
  let i = 1
  while (taken.has(`${prefix}${i}`)) i += 1
  return `${prefix}${i}`
}

/** Same ids in the same order. */
function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i])
}

/** The graph with new boxes, or the same graph when nothing changed. */
function withBoxes(graph: Graph, boxes: NetworkBox[]): Graph {
  const ann = annotationsOf(graph)
  if (boxes.length === ann.boxes.length && boxes.every((b, i) => b === ann.boxes[i])) return graph
  return { ...graph, annotations: { ...ann, boxes } }
}

/** The graph with new notes, or the same graph when nothing changed. */
function withNotes(graph: Graph, notes: StickyNote[]): Graph {
  const ann = annotationsOf(graph)
  if (notes.length === ann.notes.length && notes.every((n, i) => n === ann.notes[i])) return graph
  return { ...graph, annotations: { ...ann, notes } }
}

// ---------------------------------------------------------------------------
// Graph operations (pure; each returns the same graph when nothing changed)
// ---------------------------------------------------------------------------

/**
 * Work out box membership again for these nodes: each one leaves every box
 * and joins the smallest box (in its own network) that contains its center.
 * `positionOf` gives a node's position when it differs from the graph (a
 * drag that has not been committed yet). Members that are no longer graph
 * nodes are dropped from every box on the way.
 */
export function recomputeMembership(
  graph: Graph,
  nodeIds: readonly string[],
  sizeOf: SizeOf,
  positionOf?: (nodeId: string) => [number, number] | null,
): Graph {
  const { boxes } = annotationsOf(graph)
  if (boxes.length === 0) return graph
  const target = new Map<string, string | null>()
  for (const id of nodeIds) {
    const n = graph.nodes[id]
    if (!n) continue
    const pos = positionOf?.(id) ?? n.position
    const center = nodeCenter(pos, sizeOf(id))
    const parent = n.parent ?? null
    const inNetwork = boxes.filter(b => (b.parent ?? null) === parent)
    target.set(id, smallestBoxAt(inNetwork, center)?.id ?? null)
  }
  const next = boxes.map(b => {
    const members = b.members.filter(m => m in graph.nodes && (!target.has(m) || target.get(m) === b.id))
    for (const [id, boxId] of target) {
      if (boxId === b.id && !members.includes(id)) members.push(id)
    }
    return sameList(members, b.members) ? b : { ...b, members }
  })
  return withBoxes(graph, next)
}

/** Add a box. Its members leave any other box (the new box claims them). */
export function opAddBox(graph: Graph, box: NetworkBox): Graph {
  const ann = annotationsOf(graph)
  const claimed = new Set(box.members)
  const boxes = ann.boxes.map(b => {
    if (!b.members.some(m => claimed.has(m))) return b
    return { ...b, members: b.members.filter(m => !claimed.has(m)) }
  })
  return { ...graph, annotations: { ...ann, boxes: [...boxes, box] } }
}

/** Add a note. */
export function opAddNote(graph: Graph, note: StickyNote): Graph {
  const ann = annotationsOf(graph)
  return { ...graph, annotations: { ...ann, notes: [...ann.notes, note] } }
}

/** Change fields of one box. */
export function opUpdateBox(graph: Graph, id: string, patch: Partial<Omit<NetworkBox, 'id'>>): Graph {
  const { boxes } = annotationsOf(graph)
  return withBoxes(graph, boxes.map(b => (b.id === id && changes(b, patch) ? { ...b, ...patch } : b)))
}

/** Change fields of one note. */
export function opUpdateNote(graph: Graph, id: string, patch: Partial<Omit<StickyNote, 'id'>>): Graph {
  const { notes } = annotationsOf(graph)
  return withNotes(graph, notes.map(n => (n.id === id && changes(n, patch) ? { ...n, ...patch } : n)))
}

/** True when applying `patch` would change `obj`. */
function changes<T extends object>(obj: T, patch: Partial<T>): boolean {
  return Object.entries(patch).some(([k, v]) => JSON.stringify(obj[k as keyof T]) !== JSON.stringify(v))
}

/** Remove boxes and notes by id (graph nodes are never touched). */
export function opRemoveAnnotations(graph: Graph, ids: readonly string[]): Graph {
  const drop = new Set(ids)
  const ann = annotationsOf(graph)
  const boxes = ann.boxes.filter(b => !drop.has(b.id))
  const notes = ann.notes.filter(n => !drop.has(n.id))
  if (boxes.length === ann.boxes.length && notes.length === ann.notes.length) return graph
  return { ...graph, annotations: { boxes, notes } }
}

/**
 * Move boxes and notes to new top-left positions. A moved box takes its
 * members along by the same delta, except members in `alreadyMoved` (they
 * were dragged too and are saved by the canvas's own move).
 */
export function opMoveAnnotations(
  graph: Graph,
  moves: ReadonlyMap<string, XY>,
  alreadyMoved: ReadonlySet<string> = new Set(),
): Graph {
  if (moves.size === 0) return graph
  const ann = annotationsOf(graph)
  const nodeDelta = new Map<string, [number, number]>()
  const boxes = ann.boxes.map(b => {
    const to = moves.get(b.id)
    if (!to || (to.x === b.rect[0] && to.y === b.rect[1])) return b
    const dx = to.x - b.rect[0]
    const dy = to.y - b.rect[1]
    for (const m of b.members) {
      if (!alreadyMoved.has(m) && m in graph.nodes && !nodeDelta.has(m)) nodeDelta.set(m, [dx, dy])
    }
    return { ...b, rect: [to.x, to.y, b.rect[2], b.rect[3]] as RectTuple }
  })
  const notes = ann.notes.map(n => {
    const to = moves.get(n.id)
    if (!to || (to.x === n.rect[0] && to.y === n.rect[1])) return n
    return { ...n, rect: [to.x, to.y, n.rect[2], n.rect[3]] as RectTuple }
  })
  let next = withNotes(withBoxes(graph, boxes), notes)
  if (nodeDelta.size > 0) {
    const nodes = { ...next.nodes }
    for (const [id, [dx, dy]] of nodeDelta) {
      const n = nodes[id]
      nodes[id] = { ...n, position: [n.position[0] + dx, n.position[1] + dy] }
    }
    next = { ...next, nodes }
  }
  return next
}

/**
 * Resize a box and work out membership again for every node in its network
 * whose center is now inside it or that was a member.
 */
export function opResizeBox(graph: Graph, id: string, rect: RectTuple, sizeOf: SizeOf): Graph {
  const box = annotationsOf(graph).boxes.find(b => b.id === id)
  if (!box) return graph
  const resized = opUpdateBox(graph, id, { rect })
  const parent = box.parent ?? null
  const candidates = Object.values(resized.nodes)
    .filter(n => (n.parent ?? null) === parent)
    .filter(n => box.members.includes(n.id) || rectContains(rect, nodeCenter(n.position, sizeOf(n.id))))
    .map(n => n.id)
  return recomputeMembership(resized, candidates, sizeOf)
}

/** Grow or shrink a box to its members' bounding rect plus padding. */
export function opFitBox(graph: Graph, id: string, sizeOf: SizeOf): Graph {
  const box = annotationsOf(graph).boxes.find(b => b.id === id)
  if (!box) return graph
  const rect = boundsOfNodes(graph, box.members, sizeOf)
  return rect ? opUpdateBox(graph, id, { rect }) : graph
}

/**
 * Refit every box with a member among `movedIds` (FC-5): after a tidy moves
 * nodes, each box keeps its grouping and wraps its members again, so the
 * next box drag does not pull along members that moved far away.
 */
export function opRefitBoxesOf(graph: Graph, movedIds: Iterable<string>, sizeOf: SizeOf): Graph {
  const moved = new Set(movedIds)
  let next = graph
  for (const b of annotationsOf(graph).boxes) {
    if (b.members.some(m => moved.has(m))) next = opFitBox(next, b.id, sizeOf)
  }
  return next
}

// ---------------------------------------------------------------------------
// Slice
// ---------------------------------------------------------------------------

/** What a new box may set (the rest gets defaults). */
export type NewBox = Partial<Omit<NetworkBox, 'id'>>
/** What a new note may set (the rest gets defaults). */
export type NewNote = Partial<Omit<StickyNote, 'id'>>

export interface AnnotationsSlice {
  /** The box (label) or note (text) being edited, or null. UI state, not history. */
  editingAnnotationId: string | null
  /** Start editing a box label or note text (double-click, F2, a menu row, a new note). */
  startAnnotationEdit(id: string): void
  /** Stop editing (the component commits its text first). */
  stopAnnotationEdit(): void

  /** Add a box (one undo step). Returns its id, or null with no editable graph. */
  addBox(box?: NewBox): string | null
  /**
   * Add a note (one undo step). Returns its id, or null with no editable
   * graph. With `joinFirstEdit`, the note's first text edit (an edit with
   * `noteEditCoalesce(id)`) joins this step, and an empty first edit can
   * take the note back (UX-18: Shift+N, type, Esc is one undo step).
   */
  addNote(note?: NewNote, opts?: { joinFirstEdit?: boolean }): string | null
  /** Change a box's label, color or rect (one undo step). */
  updateBox(id: string, patch: Partial<Omit<NetworkBox, 'id'>>, label?: string): void
  /** Change a note's text, color or rect (one undo step, or joined per `opts`). */
  updateNote(id: string, patch: Partial<Omit<StickyNote, 'id'>>, label?: string, opts?: CommitOptions): void
  /** Resize a box and work out its membership again (one undo step). */
  resizeBox(id: string, rect: RectTuple, sizeOf?: SizeOf): void
  /** Fit a box to its members plus 24px (one undo step). */
  fitBox(id: string, sizeOf?: SizeOf): void
  /** Delete boxes and notes; nodes stay (one undo step). */
  removeAnnotations(ids: readonly string[]): void
}

const noSizes: SizeOf = () => null

/** The coalesce key that joins a new note and its first text edit (UX-18). */
export function noteEditCoalesce(id: string): string {
  return `note-create:${id}`
}

// The box or note in edit is gone after a commit, undo or redo: stop editing
// in the same store write (EA-5), so a later note with the same id does not
// open in edit.
registerGraphReconciler((s, next) => {
  const id = s.editingAnnotationId
  if (!id || !next) return null
  const a = next.annotations
  const there = (a?.boxes ?? []).some(b => b.id === id) || (a?.notes ?? []).some(n => n.id === id)
  return there ? null : { editingAnnotationId: null }
})

export const createAnnotationsSlice: StateCreator<NodeBuilderState, [], [], AnnotationsSlice> = (set, get) => {
  /** The store graph when it can be edited, else null. */
  const editableGraph = (): Graph | null => {
    const g = get().graph
    return g && !g.readOnly ? g : null
  }

  return {
    editingAnnotationId: null,

    startAnnotationEdit(id) {
      if (get().editingAnnotationId !== id) set({ editingAnnotationId: id })
    },

    stopAnnotationEdit() {
      if (get().editingAnnotationId !== null) set({ editingAnnotationId: null })
    },

    addBox(input = {}) {
      const g = editableGraph()
      if (!g) return null
      const id = newAnnotationId(g, 'box')
      const box: NetworkBox = {
        id,
        label: input.label ?? '',
        color: input.color ?? 'network',
        rect: input.rect ?? [0, 0, BOX_DEFAULT_SIZE.w, BOX_DEFAULT_SIZE.h],
        members: (input.members ?? []).filter(m => m in g.nodes),
        // The network on screen (EA-4).
        parent: input.parent !== undefined ? input.parent : currentParentId(get(), g),
      }
      get().commit('add network box', graph => opAddBox(graph, box))
      return id
    },

    addNote(input = {}, opts) {
      const g = editableGraph()
      if (!g) return null
      const id = newAnnotationId(g, 'note')
      const note: StickyNote = {
        id,
        text: (input.text ?? '').slice(0, NOTE_TEXT_MAX),
        rect: input.rect ?? [0, 0, NOTE_DEFAULT_SIZE.w, NOTE_DEFAULT_SIZE.h],
        color: input.color ?? 'amber',
        parent: input.parent !== undefined ? input.parent : currentParentId(get(), g),
      }
      get().commit('add sticky note', graph => opAddNote(graph, note),
        opts?.joinFirstEdit ? { coalesce: noteEditCoalesce(id), windowMs: Infinity } : undefined)
      return id
    },

    updateBox(id, patch, label = 'edit network box') {
      if (!editableGraph()) return
      const safe = patch.label !== undefined ? { ...patch, label: patch.label.slice(0, BOX_LABEL_MAX) } : patch
      get().commit(label, graph => opUpdateBox(graph, id, safe))
    },

    updateNote(id, patch, label = 'edit note', opts) {
      if (!editableGraph()) return
      const safe = patch.text !== undefined ? { ...patch, text: patch.text.slice(0, NOTE_TEXT_MAX) } : patch
      get().commit(label, graph => opUpdateNote(graph, id, safe), opts)
    },

    resizeBox(id, rect, sizeOf = noSizes) {
      if (!editableGraph()) return
      get().commit('resize network box', graph => opResizeBox(graph, id, rect, sizeOf))
    },

    fitBox(id, sizeOf = noSizes) {
      if (!editableGraph()) return
      get().commit('fit network box', graph => opFitBox(graph, id, sizeOf))
    },

    removeAnnotations(ids) {
      if (!editableGraph() || ids.length === 0) return
      get().commit(ids.length === 1 ? 'delete annotation' : 'delete annotations', graph => opRemoveAnnotations(graph, ids))
      const editing = get().editingAnnotationId
      if (editing && ids.includes(editing)) set({ editingAnnotationId: null })
    },
  }
}
