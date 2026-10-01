/**
 * Clipboard commands (F435 W3 item 3.C, foundation 6.2, S19 menus):
 * Cmd+C copy, Cmd+X cut, Cmd+V paste at the cursor, Cmd+D duplicate 24px
 * down and right. Alt-drag duplicate is a canvas plugin
 * (plugins/altDragDuplicate.ts).
 *
 * Canvas scope: the canvas only dispatches them while it has the keys and
 * no text field has focus, so Cmd+C in a param field still copies text.
 * Paste, cut and duplicate never run on a read-only graph. Copy works on
 * one (from a menu), so a view can be copied into an editable graph.
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import type { CanvasCtx } from '../canvasPlugins'
import { describePayload } from '../clipboard'
import type { NodeBuilderState } from '../store'
import { canEdit, canPaste } from '../store/clipboard'
import type { Command } from './index'

/** True when something (a node, a box, a note) is selected. */
function hasSelection(s: NodeBuilderState): boolean {
  return s.selectedNodeIds.length > 0 || s.selectedAnnotationIds.length > 0
}

/** The canvas may be showing a graph that is not the store's (the read-only view). */
function canvasEditable(canvas: CanvasCtx | null): boolean {
  return !canvas || canvas.editable()
}

/**
 * True when the page has selected text outside the canvas (a description
 * in a panel). Cmd+C then copies that text, the browser way.
 */
function textSelectedOutside(canvas: CanvasCtx | null): boolean {
  const sel = typeof window !== 'undefined' ? window.getSelection?.() : null
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return false
  const root = canvas?.container()
  return !(root && sel.anchorNode && root.contains(sel.anchorNode))
}

export const commands: Command[] = [
  {
    id: 'clipboard.copy',
    readOnlyOk: true,
    label: 'Copy',
    keys: ['mod+c'],
    scope: 'canvas',
    menu: ['node', 'box', 'note'],
    when: hasSelection,
    disabledReason: s => (hasSelection(s) ? null : 'Select something first'),
    run({ canvas, store, event }) {
      if (event && textSelectedOutside(canvas)) return false
      const s = store.getState()
      const payload = s.copySelection(canvas ? canvas.graph() : s.graph)
      if (!payload) return false
      s.showFlash(`Copied ${describePayload(payload)}`)
    },
  },
  {
    id: 'clipboard.cut',
    label: 'Cut',
    keys: ['mod+x'],
    scope: 'canvas',
    menu: ['node', 'box', 'note'],
    when: s => canEdit(s) && hasSelection(s),
    disabledReason: s => (!canEdit(s) ? 'This graph is read-only' : hasSelection(s) ? null : 'Select something first'),
    run({ canvas, store, event }) {
      if (!canvas || !canvas.editable()) return false
      if (event && textSelectedOutside(canvas)) return false
      if (!store.getState().copySelection()) return false
      // Same as Delete, so boxes and notes go too and it is one undo step.
      return canvas.deleteSelection({ rewire: true })
    },
  },
  {
    id: 'clipboard.paste',
    label: 'Paste',
    keys: ['mod+v'],
    scope: 'canvas',
    menu: ['pane', 'node'],
    when: canPaste,
    disabledReason: s => (!canEdit(s) ? 'This graph is read-only' : canPaste(s) ? null : 'Nothing to paste'),
    run({ canvas, store }) {
      if (!canvasEditable(canvas)) return false
      // At the cursor (a context menu: where it was opened). No canvas:
      // where the copy came from.
      const at = canvas ? canvas.pointer() : null
      return store.getState().pasteClipboard(at) !== null
    },
  },
  {
    id: 'clipboard.duplicate',
    label: 'Duplicate',
    keys: ['mod+d'],
    scope: 'canvas',
    menu: ['node', 'box', 'note'],
    when: s => canEdit(s) && hasSelection(s),
    disabledReason: s => (!canEdit(s) ? 'This graph is read-only' : hasSelection(s) ? null : 'Select something first'),
    run({ canvas, store }) {
      if (!canvasEditable(canvas)) return false
      return store.getState().duplicateSelection() !== null
    },
  },
]
