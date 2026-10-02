/**
 * Network box and sticky note commands (item 3.E, specs S17, S18, S19).
 *
 * Keys: Shift+B new box (around the selection, or empty at the cursor),
 * Shift+N new note at the cursor (opens in edit mode), F2 edits the
 * selected box label or note text. The rest are right-click menu rows for
 * the `box` and `note` menus (S19); they act on the selected boxes or notes
 * (a right-click selects the item first).
 *
 * Tint and color rows carry two extra fields for the context menu:
 * `submenu` ('Tint' or 'Color') groups them under one `▸` row, and `swatch`
 * is the CSS color of the square to draw. Each edit is one undo step.
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { Graph } from '../../../api/nodebuilder'
import type { NodeBuilderState } from '../store'
import type { Command, CommandCtx } from './index'
import {
  BOX_DEFAULT_SIZE,
  BOX_TINTS,
  NOTE_COLORS,
  NOTE_DEFAULT_SIZE,
  annotationsOf,
  boundsOfNodes,
  boxTint,
  boxTintVar,
  noteColor,
  rfSizeOf,
  type SizeOf,
} from '../store/annotations'

/** A command with the context-menu extras described above. */
export interface AnnotationCommand extends Command {
  /** Rows with the same submenu are shown under one `<submenu> ▸` row. */
  submenu?: 'Tint' | 'Color'
  /** CSS color of the swatch square for this row. */
  swatch?: string
}

/** True when the store holds an editable graph. */
function editing(s: NodeBuilderState): boolean {
  return s.graph != null && !s.graph.readOnly
}

function selectedBoxIds(s: NodeBuilderState): string[] {
  if (!s.graph) return []
  const ids = new Set(s.selectedAnnotationIds)
  return annotationsOf(s.graph).boxes.filter(b => ids.has(b.id)).map(b => b.id)
}

function selectedNoteIds(s: NodeBuilderState): string[] {
  if (!s.graph) return []
  const ids = new Set(s.selectedAnnotationIds)
  return annotationsOf(s.graph).notes.filter(n => ids.has(n.id)).map(n => n.id)
}

const hasBox = (s: NodeBuilderState) => editing(s) && selectedBoxIds(s).length > 0
const hasNote = (s: NodeBuilderState) => editing(s) && selectedNoteIds(s).length > 0
const boxReason = (s: NodeBuilderState) => (hasBox(s) ? null : 'Select a box first')
const noteReason = (s: NodeBuilderState) => (hasNote(s) ? null : 'Select a note first')

function sizesFrom(ctx: CommandCtx): SizeOf {
  return ctx.canvas ? rfSizeOf(ctx.canvas.rf) : () => null
}

/** Run `fn` for every selected box (or note) as one undo step. */
function forEach(ctx: CommandCtx, ids: string[], label: string, fn: (id: string) => void): void {
  const s = ctx.store.getState()
  s.beginBatch(label)
  try {
    for (const id of ids) fn(id)
  } finally {
    s.endBatch()
  }
}

/** Where a new box or note goes: its top-left at the pointer (flow units). */
function pointerSpot(ctx: CommandCtx): { x: number; y: number } {
  const p = ctx.canvas?.pointer() ?? { x: 0, y: 0 }
  return { x: Math.round(p.x), y: Math.round(p.y) }
}

/** Shift+B: a box around the selected nodes, or an empty one at the cursor. */
function newBox(ctx: CommandCtx): boolean {
  const s = ctx.store.getState()
  const graph: Graph | null = s.graph
  if (!graph || graph.readOnly) return false
  const members = s.selectedNodeIds.filter(id => id in graph.nodes)
  let rect = members.length ? boundsOfNodes(graph, members, sizesFrom(ctx)) : null
  if (!rect) {
    const p = pointerSpot(ctx)
    rect = [p.x, p.y, BOX_DEFAULT_SIZE.w, BOX_DEFAULT_SIZE.h]
  }
  const id = s.addBox({ rect, members })
  if (id) s.setSelection({ annotationIds: [id] })
  return id != null
}

/** Shift+N: a note at the cursor, straight into edit mode. */
function newNote(ctx: CommandCtx): boolean {
  const s = ctx.store.getState()
  if (!s.graph || s.graph.readOnly) return false
  const p = pointerSpot(ctx)
  // The note and the text typed into it right away are one undo step (UX-18).
  const id = s.addNote({ rect: [p.x, p.y, NOTE_DEFAULT_SIZE.w, NOTE_DEFAULT_SIZE.h] }, { joinFirstEdit: true })
  if (!id) return false
  s.setSelection({ annotationIds: [id] })
  s.startAnnotationEdit(id)
  return true
}

const tintCommands: AnnotationCommand[] = BOX_TINTS.map(tint => ({
  id: `annotations.tintBox.${tint}`,
  label: tint[0].toUpperCase() + tint.slice(1),
  menu: 'box',
  submenu: 'Tint',
  swatch: boxTintVar(tint),
  when: hasBox,
  disabledReason: boxReason,
  checked: (s: NodeBuilderState) => {
    const ids = new Set(selectedBoxIds(s))
    const boxes = s.graph ? annotationsOf(s.graph).boxes.filter(b => ids.has(b.id)) : []
    return boxes.length > 0 && boxes.every(b => boxTint(b.color) === tint)
  },
  run(ctx: CommandCtx) {
    const ids = selectedBoxIds(ctx.store.getState())
    if (ids.length === 0) return false
    forEach(ctx, ids, 'tint network box', id => ctx.store.getState().updateBox(id, { color: tint }, 'tint network box'))
  },
}))

const NOTE_SWATCH: Record<string, string> = {
  amber: 'var(--nb-note-bg)',
  blue: '#12243a',
  green: '#12301f',
  grey: '#1a1f28',
}

const colorCommands: AnnotationCommand[] = NOTE_COLORS.map(color => ({
  id: `annotations.noteColor.${color}`,
  label: color[0].toUpperCase() + color.slice(1),
  menu: 'note',
  submenu: 'Color',
  swatch: NOTE_SWATCH[color],
  when: hasNote,
  disabledReason: noteReason,
  checked: (s: NodeBuilderState) => {
    const ids = new Set(selectedNoteIds(s))
    const notes = s.graph ? annotationsOf(s.graph).notes.filter(n => ids.has(n.id)) : []
    return notes.length > 0 && notes.every(n => noteColor(n.color) === color)
  },
  run(ctx: CommandCtx) {
    const ids = selectedNoteIds(ctx.store.getState())
    if (ids.length === 0) return false
    forEach(ctx, ids, 'color note', id => ctx.store.getState().updateNote(id, { color }, 'color note'))
  },
}))

export const commands: AnnotationCommand[] = [
  {
    id: 'annotations.newBox',
    label: 'Network box',
    keys: ['shift+b'],
    menu: 'pane',
    when: editing,
    run: newBox,
  },
  {
    id: 'annotations.newNote',
    label: 'Sticky note',
    keys: ['shift+n'],
    menu: 'pane',
    when: editing,
    run: newNote,
  },
  {
    // F2 on a selected box or note. With a graph node selected it does not
    // act, so a node rename on F2 (registered later) still gets the key.
    id: 'annotations.edit',
    label: 'Rename box or edit note',
    keys: ['f2'],
    when: editing,
    run(ctx) {
      const s = ctx.store.getState()
      if (s.selectedNodeIds.length > 0) return false
      const id = [...selectedBoxIds(s), ...selectedNoteIds(s)][0]
      if (!id) return false
      s.startAnnotationEdit(id)
    },
  },
  {
    id: 'annotations.renameBox',
    label: 'Rename',
    menu: 'box',
    when: hasBox,
    disabledReason: boxReason,
    run(ctx) {
      const id = selectedBoxIds(ctx.store.getState())[0]
      if (!id) return false
      ctx.store.getState().startAnnotationEdit(id)
    },
  },
  ...tintCommands,
  {
    id: 'annotations.fitBox',
    label: 'Fit to contents',
    menu: 'box',
    when: hasBox,
    disabledReason: boxReason,
    run(ctx) {
      const s = ctx.store.getState()
      const ids = selectedBoxIds(s)
      if (ids.length === 0) return false
      const empty = ids.every(id => {
        const box = annotationsOf(s.graph!).boxes.find(b => b.id === id)
        return !box || !box.members.some(m => m in s.graph!.nodes)
      })
      if (empty) { s.showFlash('The box has no nodes to fit'); return }
      const sizeOf = sizesFrom(ctx)
      forEach(ctx, ids, 'fit network box', id => ctx.store.getState().fitBox(id, sizeOf))
    },
  },
  {
    id: 'annotations.deleteBox',
    label: 'Delete box (keeps nodes)',
    menu: 'box',
    when: hasBox,
    disabledReason: boxReason,
    run(ctx) {
      const ids = selectedBoxIds(ctx.store.getState())
      if (ids.length === 0) return false
      ctx.store.getState().removeAnnotations(ids)
    },
  },
  {
    id: 'annotations.editNote',
    label: 'Edit',
    menu: 'note',
    when: hasNote,
    disabledReason: noteReason,
    run(ctx) {
      const id = selectedNoteIds(ctx.store.getState())[0]
      if (!id) return false
      ctx.store.getState().startAnnotationEdit(id)
    },
  },
  ...colorCommands,
  {
    id: 'annotations.deleteNote',
    label: 'Delete',
    menu: 'note',
    when: hasNote,
    disabledReason: noteReason,
    run(ctx) {
      const ids = selectedNoteIds(ctx.store.getState())
      if (ids.length === 0) return false
      ctx.store.getState().removeAnnotations(ids)
    },
  },
]
