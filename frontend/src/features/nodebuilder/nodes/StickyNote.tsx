/**
 * Sticky note (spec S18, item 3.E): a short plain-text note on the canvas.
 * Stored in `graph.annotations.notes`; drawn as the React Flow node type
 * `nbNote` (registered at the bottom of this file). Notes never take wires
 * and never change evaluation.
 *
 * Editing: double-click, F2 or a new note (Shift+N) opens a textarea. Esc,
 * Cmd+Enter and blur keep the text, as ONE commit per edit. Plain Enter is a
 * new line and Tab types two spaces. The textarea is a text field, so the
 * canvas keys (B, D, Delete, Space) type instead of acting.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { NodeResizeControl, type Node as RFNode, type NodeProps } from '@xyflow/react'
import type { StickyNote as StickyNoteModel } from '../../../api/nodebuilder'
import { registerNodeType, type NodeComponent } from '../nodeTypes'
import { useNodeBuilderStore } from '../store'
import { NOTE_MAX_SIZE, NOTE_MIN_SIZE, NOTE_TEXT_MAX, noteColor, noteEditCoalesce } from '../store/annotations'
import { focusCanvasRoot, useFullMarqueeOnly } from './annotationUi'
import './annotations.css'

/** What the node source puts in a note node's `data`. */
export interface NoteNodeData extends Record<string, unknown> {
  note: StickyNoteModel
  editable: boolean
}

export type NoteRFNode = RFNode<NoteNodeData, 'nbNote'>

export default function StickyNote({ id, data, selected }: NodeProps<NoteRFNode>) {
  const { note, editable } = data
  useFullMarqueeOnly(id, note.rect, !!selected)
  const editing = useNodeBuilderStore(s => s.editingAnnotationId === id) && editable
  const updateNote = useNodeBuilderStore(s => s.updateNote)
  const startEdit = useNodeBuilderStore(s => s.startAnnotationEdit)
  const stopEdit = useNodeBuilderStore(s => s.stopAnnotationEdit)

  const color = noteColor(note.color)
  const className = [
    'nb-note',
    color !== 'amber' && `nb-note--${color}`,
    selected && 'nb-note--selected',
    editing && 'nb-note--editing',
  ].filter(Boolean).join(' ')

  return (
    <div
      className={className}
      role="note"
      aria-label={note.text.slice(0, 60) || 'Empty sticky note'}
      data-testid={`nb-note-${id}`}
      onDoubleClick={(e) => {
        if (!editable || editing) return
        e.stopPropagation()
        startEdit(id)
      }}
    >
      {editing ? (
        <NoteEditor
          initial={note.text}
          onDone={(text, el) => {
            focusCanvasRoot(el)
            stopEdit()
            // A note made by Shift+N: its first text joins the create step,
            // and leaving it empty takes the note back (UX-18). Any other
            // edit is its own step (the run is over once anything else
            // was committed).
            const key = noteEditCoalesce(id)
            if (text !== note.text) updateNote(id, { text }, 'edit note', { coalesce: key, windowMs: Infinity, last: true })
            else if (text === '') useNodeBuilderStore.getState().dropCoalesced(key)
          }}
        />
      ) : (
        <>
          <div className={`nb-note__text${note.text ? '' : ' nb-note__text--empty'}`}>
            {note.text || 'Double-click to write'}
          </div>
          <div className="nb-note__fade" />
        </>
      )}

      {editable && !editing && (
        <NodeResizeControl
          position="bottom-right"
          className="nb-note__grip"
          minWidth={NOTE_MIN_SIZE.w}
          minHeight={NOTE_MIN_SIZE.h}
          maxWidth={NOTE_MAX_SIZE.w}
          maxHeight={NOTE_MAX_SIZE.h}
          onResizeEnd={(_e, p) => updateNote(id, { rect: [p.x, p.y, p.width, p.height] }, 'resize note')}
        >
          <span className="nb-note__grip-mark" />
        </NodeResizeControl>
      )}
    </div>
  )
}

/** The textarea that fills the note while editing. */
function NoteEditor({ initial, onDone }: { initial: string; onDone(text: string, el: HTMLElement | null): void }) {
  const [text, setText] = useState(initial)
  const ref = useRef<HTMLTextAreaElement>(null)
  // Set once Esc or Cmd+Enter has finished the edit, so the blur that
  // follows (the textarea unmounts) does not commit a second time.
  const doneRef = useRef(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])
  const finish = (value: string) => {
    if (doneRef.current) return
    doneRef.current = true
    onDone(value, ref.current)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd/Ctrl+Enter ends the edit (S18); handled here, and its default is
    // prevented, so the global Run key does not also fire.
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      e.stopPropagation()
      finish(e.currentTarget.value)
      return
    }
    // Any other Cmd/Ctrl chord goes on to the builder's global keys (Cmd+S
    // saves instead of opening the browser's Save Page; UX-03). Global keys
    // that are not `inFields` stay off while a field has focus, so the
    // note's own Cmd+Z and Cmd+A still work. Plain keys belong to the note.
    if (e.metaKey || e.ctrlKey) return
    e.stopPropagation()
    if (e.key === 'Escape') {
      e.preventDefault()
      finish(e.currentTarget.value)
    } else if (e.key === 'Tab' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      // Two spaces instead of moving focus.
      e.preventDefault()
      const el = e.currentTarget
      const start = el.selectionStart
      const end = el.selectionEnd
      const next = (el.value.slice(0, start) + '  ' + el.value.slice(end)).slice(0, NOTE_TEXT_MAX)
      setText(next)
      requestAnimationFrame(() => {
        if (ref.current) ref.current.selectionStart = ref.current.selectionEnd = Math.min(start + 2, next.length)
      })
    }
  }
  return (
    <textarea
      ref={ref}
      className="nb-note__textarea nodrag nowheel nopan"
      aria-label="Sticky note text"
      data-testid="nb-note-textarea"
      value={text}
      maxLength={NOTE_TEXT_MAX}
      onChange={e => setText(e.target.value)}
      onKeyDown={onKeyDown}
      onBlur={e => finish(e.currentTarget.value)}
    />
  )
}

registerNodeType('nbNote', StickyNote as NodeComponent)
