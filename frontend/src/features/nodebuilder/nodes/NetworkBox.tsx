/**
 * Network box (spec S17, item 3.E): a labeled, tinted rectangle drawn under
 * a group of nodes. It moves its members when dragged and never changes
 * evaluation. Stored in `graph.annotations.boxes`; drawn as the React Flow
 * node type `nbBox` (registered at the bottom of this file).
 *
 * Moving members and working out membership live in plugins/boxDrag.ts.
 * This file draws the box, edits its label and commits resizes.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import {
  NodeResizeControl,
  ResizeControlVariant,
  useReactFlow,
  type ControlPosition,
  type Node as RFNode,
  type NodeProps,
  type OnResizeEnd,
} from '@xyflow/react'
import type { NetworkBox as NetworkBoxModel } from '../../../api/nodebuilder'
import { registerNodeType, type NodeComponent } from '../nodeTypes'
import { useNodeBuilderStore } from '../store'
import { BOX_LABEL_MAX, BOX_MIN_SIZE, boxTintVar } from '../store/annotations'
import { rfSizeOf } from '../geometry'
import { focusCanvasRoot, useFullMarqueeOnly, useIsHotBox } from './annotationUi'
import './annotations.css'

/** What the node source puts in a box node's `data`. */
export interface BoxNodeData extends Record<string, unknown> {
  box: NetworkBoxModel
  /** Members that are still graph nodes. */
  memberCount: number
  editable: boolean
}

export type BoxRFNode = RFNode<BoxNodeData, 'nbBox'>

const CORNERS: ControlPosition[] = ['top-left', 'top-right', 'bottom-left', 'bottom-right']
const EDGES: ControlPosition[] = ['top', 'right', 'bottom', 'left']

export default function NetworkBox({ id, data, selected }: NodeProps<BoxRFNode>) {
  const { box, memberCount, editable } = data
  const rf = useReactFlow()
  const hot = useIsHotBox(id)
  useFullMarqueeOnly(id, box.rect, !!selected)
  const editing = useNodeBuilderStore(s => s.editingAnnotationId === id) && editable
  const updateBox = useNodeBuilderStore(s => s.updateBox)
  const resizeBox = useNodeBuilderStore(s => s.resizeBox)
  const startEdit = useNodeBuilderStore(s => s.startAnnotationEdit)
  const stopEdit = useNodeBuilderStore(s => s.stopAnnotationEdit)
  // One stable handler for all eight controls, so a box re-render (hot box,
  // selection) does not re-run each control's resizer update (IP-8).
  const onResizeEnd = useCallback<OnResizeEnd>(
    (_e, p) => resizeBox(id, [p.x, p.y, p.width, p.height], rfSizeOf(rf)),
    [id, resizeBox, rf],
  )

  const shown = box.label.trim()
  const style = { '--nb-box-tint': boxTintVar(box.color) } as CSSProperties
  const className = ['nb-box', selected && 'nb-box--selected', hot && 'nb-box--hot'].filter(Boolean).join(' ')

  return (
    <div
      className={className}
      style={style}
      role="group"
      aria-label={`Network box ${shown || 'BOX'}, ${memberCount} ${memberCount === 1 ? 'node' : 'nodes'}`}
      data-testid={`nb-box-${id}`}
    >
      {editing ? (
        <div className="nb-box__tab">
          <LabelInput
            initial={box.label}
            onDone={(value, el) => {
              focusCanvasRoot(el)
              stopEdit()
              if (value !== null && value !== box.label) updateBox(id, { label: value }, 'rename network box')
            }}
          />
        </div>
      ) : (
        <div
          className={`nb-box__tab${shown ? '' : ' nb-box__tab--empty'}`}
          data-testid={`nb-box-label-${id}`}
          title="Network box · double-click to rename"
          onDoubleClick={(e) => {
            if (!editable) return
            e.stopPropagation()
            startEdit(id)
          }}
        >
          {shown || 'BOX'}
        </div>
      )}

      {editable && (
        <>
          {EDGES.map(pos => (
            <NodeResizeControl
              key={pos}
              position={pos}
              variant={ResizeControlVariant.Line}
              className="nb-box__edge"
              minWidth={BOX_MIN_SIZE.w}
              minHeight={BOX_MIN_SIZE.h}
              onResizeEnd={onResizeEnd}
            />
          ))}
          {CORNERS.map(pos => (
            <NodeResizeControl
              key={pos}
              position={pos}
              className="nb-box__grip"
              // The grips are styled in annotations.css; no zoom subscription
              // (autoScale re-renders each corner on every zoom frame; IP-8).
              autoScale={false}
              minWidth={BOX_MIN_SIZE.w}
              minHeight={BOX_MIN_SIZE.h}
              onResizeEnd={onResizeEnd}
            >
              <span className="nb-box__grip-mark" data-testid={`nb-box-grip-${pos}`} />
            </NodeResizeControl>
          ))}
        </>
      )}
    </div>
  )
}

/**
 * The label editor: Enter and blur keep the text, Esc puts the old label
 * back (onDone(null)). The stored label keeps the typed case; the caps are
 * only CSS.
 */
function LabelInput({ initial, onDone }: { initial: string; onDone(value: string | null, el: HTMLElement | null): void }) {
  const [value, setValue] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)
  // Set once Enter or Esc has finished the edit, so the blur that follows
  // (the input unmounts) does not finish it a second time.
  const doneRef = useRef(false)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const finish = (v: string | null) => {
    if (doneRef.current) return
    doneRef.current = true
    onDone(v, ref.current)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // A chord with Cmd/Ctrl goes on to the builder's global keys (Cmd+S,
    // Cmd+Enter: the field is blurred, which keeps the label, then the
    // command runs). Plain keys belong to the field (UX-03).
    if (e.metaKey || e.ctrlKey) return
    e.stopPropagation()
    if (e.key === 'Enter') { e.preventDefault(); finish(value) }
    else if (e.key === 'Escape') { e.preventDefault(); finish(null) }
  }
  return (
    <input
      ref={ref}
      className="nb-box__input nodrag nopan"
      aria-label="Box label"
      value={value}
      maxLength={BOX_LABEL_MAX}
      size={Math.max(4, value.length + 1)}
      onChange={e => setValue(e.target.value)}
      onKeyDown={onKeyDown}
      onBlur={() => finish(value)}
    />
  )
}

registerNodeType('nbBox', NetworkBox as NodeComponent)
