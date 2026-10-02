/**
 * Canvas render smoke (F435 Wave 0 item 0.C): the editable canvas mounts
 * without a render loop, the Entry/Exit nodes use the 'nbOutput' React Flow
 * type (not React Flow's white default 'output'), and Tab / Delete reach the
 * canvas when focus is on the page body.
 */

import { describe, it, expect, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { useRef } from 'react'
import Canvas from '../Canvas'
import { useGlobalKeys } from '../commands/useGlobalKeys'
import { useNodeBuilderStore } from '../store'
import type { Graph } from '../../../api/nodebuilder'

function makeGraph(): Graph {
  const node = (id: string, type: string, params: Record<string, string | number>, y: number) => ({
    id, type, name: id, parent: null, params, position: [0, y] as [number, number], display: false, bypass: false,
  })
  return {
    _version: 2,
    stream_schema: 1,
    readOnly: false,
    meta: {},
    nodes: {
      t: node('t', 'ticker', { symbol: 'AAPL', interval: '1d', source: 'yahoo' }, 0),
      r: node('r', 'rsi', { period: 14 }, 150),
      e: node('e', 'entry', {}, 300),
    },
    wires: [
      { id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0', attr: '@close' },
      { id: 'w2', from: 'r', to: 'e', from_port: 'out', to_port: 'in0', attr: '@rsi' },
    ],
    annotations: { boxes: [], notes: [] },
  }
}

beforeAll(() => {
  // React Flow reads this in jsdom; a no-op stand-in is enough here.
  if (!(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor() {}
    }
  }
})

afterEach(() => {
  cleanup()
  useNodeBuilderStore.getState().discardEdits()
})

/** The builder root as NodeBuilder has it: global keys (Cmd+Z) are dispatched here. */
function BuilderRoot({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useGlobalKeys(ref)
  return (
    <div ref={ref} className="nodebuilder-root" style={{ width: 800, height: 600 }}>
      {children}
    </div>
  )
}

function mount() {
  const g = makeGraph()
  useNodeBuilderStore.getState().openGraph(g, { id: null, rev: 0, name: 'test' })
  const utils = render(
    <BuilderRoot>
      <Canvas graph={g} />
    </BuilderRoot>,
  )
  // jsdom has no layout, so give the root and the canvas a client rect to count as "in view".
  const root = utils.container.querySelector('.nodebuilder-root') as HTMLElement
  root.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  const canvas = utils.container.querySelector('.nodebuilder-root .nodebuilder-root') as HTMLElement
  canvas.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  return { ...utils, canvas, root }
}

describe('Canvas (editable)', () => {
  it('mounts and renders Entry with the nbOutput node type', () => {
    const { container } = mount()
    expect(container.querySelector('.react-flow__node-nbOutput')).not.toBeNull()
    expect(container.querySelector('.react-flow__node-output')).toBeNull()
  })

  it('opens the Tab menu from the page body', () => {
    mount()
    ;(document.activeElement as HTMLElement | null)?.blur()
    act(() => { fireEvent.keyDown(document.body, { key: 'Tab' }) })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('deletes the selected node on Delete', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete' }) })
    const graph = useNodeBuilderStore.getState().graph!
    expect(graph.nodes.r).toBeUndefined()
    // Rewire bridged the gap left by the deleted node.
    expect(graph.wires.some(w => w.from === 't' && w.to === 'e')).toBe(true)
  })

  function tabCreate(query: string) {
    act(() => { fireEvent.keyDown(document.body, { key: 'Tab' }) })
    const input = screen.getByRole('dialog').querySelector('input') as HTMLInputElement
    act(() => { fireEvent.change(input, { target: { value: query } }) })
    act(() => { fireEvent.keyDown(input, { key: 'Enter' }) })
  }

  it('adds no wire out of a selected Entry when Tab creates a node (UXP-1)', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('e') })
    const before = useNodeBuilderStore.getState().graph!.wires.length
    tabCreate('sma')
    const graph = useNodeBuilderStore.getState().graph!
    expect(Object.values(graph.nodes).some(n => n.type === 'sma')).toBe(true)
    expect(graph.wires).toHaveLength(before)
    expect(graph.wires.some(w => w.from === 'e')).toBe(false)
  })

  it('adds no wire into a new Ticker from the selected node (FC-1)', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    const before = useNodeBuilderStore.getState().graph!.wires.length
    tabCreate('ticker')
    const graph = useNodeBuilderStore.getState().graph!
    expect(Object.values(graph.nodes).filter(n => n.type === 'ticker')).toHaveLength(2)
    expect(graph.wires).toHaveLength(before)
  })

  it('still auto-wires from a selected RSI into a new comparison', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    tabCreate('above')
    const graph = useNodeBuilderStore.getState().graph!
    const above = Object.values(graph.nodes).find(n => n.type === 'above')!
    expect(graph.wires.some(w => w.from === 'r' && w.to === above.id)).toBe(true)
  })

  it('ignores Backspace on the page body after a click outside the node builder (UXP-4)', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    const outside = document.createElement('div')
    document.body.appendChild(outside)
    act(() => { fireEvent.pointerDown(outside) })
    ;(document.activeElement as HTMLElement | null)?.blur()
    act(() => { fireEvent.keyDown(document.body, { key: 'Backspace' }) })
    expect(useNodeBuilderStore.getState().graph!.nodes.r).toBeDefined()
    outside.remove()
  })

  it('leaves Shift+Tab to the browser (FC-9)', () => {
    mount()
    ;(document.activeElement as HTMLElement | null)?.blur()
    act(() => { fireEvent.keyDown(document.body, { key: 'Tab', shiftKey: true }) })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  // ── Wave 1: history keys go through the command registry ────────────────

  it('Cmd+Z undoes a Delete and Cmd+Shift+Z redoes it', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete' }) })
    expect(useNodeBuilderStore.getState().graph!.nodes.r).toBeUndefined()
    act(() => { fireEvent.keyDown(document.body, { key: 'z', metaKey: true }) })
    expect(useNodeBuilderStore.getState().graph!.nodes.r).toBeDefined()
    act(() => { fireEvent.keyDown(document.body, { key: 'z', metaKey: true, shiftKey: true }) })
    expect(useNodeBuilderStore.getState().graph!.nodes.r).toBeUndefined()
  })

  it("a press in one of the builder's portals (a dialog) keeps keys with the canvas; a press outside does not (UX-03)", () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    act(() => { fireEvent.pointerDown(outside) })
    ;(document.activeElement as HTMLElement | null)?.blur()
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete' }) })
    expect(useNodeBuilderStore.getState().graph!.nodes.r).toBeDefined()
    // Now a press inside a dialog portal (it carries the nodebuilder-root class).
    const portal = document.createElement('div')
    portal.className = 'nodebuilder-root nb-dialog-backdrop'
    const inDialog = document.createElement('button')
    portal.appendChild(inDialog)
    document.body.appendChild(portal)
    act(() => { fireEvent.pointerDown(inDialog) })
    portal.remove()
    outside.remove()
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete' }) })
    expect(useNodeBuilderStore.getState().graph!.nodes.r).toBeUndefined()
  })

  it('Shift+Delete deletes without reconnecting', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    act(() => { fireEvent.keyDown(document.body, { key: 'Delete', shiftKey: true }) })
    const graph = useNodeBuilderStore.getState().graph!
    expect(graph.nodes.r).toBeUndefined()
    expect(graph.wires).toHaveLength(0)
  })

  it('a Tab create with its auto-wire is one undo step', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().select('r') })
    const before = useNodeBuilderStore.getState().graph
    tabCreate('above')
    const s = useNodeBuilderStore.getState()
    expect(s.past).toHaveLength(1)
    const above = Object.values(s.graph!.nodes).find(n => n.type === 'above')!
    expect(above.id).toMatch(/^n_[a-z0-9]{8}$/)
    expect(above.name).toBe('above')
    expect(s.graph!.wires.find(w => w.to === above.id)?.to_port).toBe('in0')
    act(() => { fireEvent.keyDown(document.body, { key: 'z', metaKey: true }) })
    expect(useNodeBuilderStore.getState().graph).toBe(before)
  })

  it('leaves Cmd+Z in a text field to the field', () => {
    mount()
    act(() => { useNodeBuilderStore.getState().moveNode('t', [5, 5]) })
    const input = document.createElement('input')
    document.querySelector('.nodebuilder-root')!.appendChild(input)
    input.focus()
    act(() => { fireEvent.keyDown(input, { key: 'z', metaKey: true }) })
    expect(useNodeBuilderStore.getState().graph!.nodes.t.position).toEqual([5, 5])
    input.remove()
  })

  it('a press inside the canvas focuses the canvas root (spec 0.8)', () => {
    const { canvas } = mount()
    const button = document.createElement('button')
    document.body.appendChild(button)
    button.focus()
    const pane = canvas.querySelector('.react-flow__pane') as HTMLElement
    act(() => { fireEvent.pointerDown(pane) })
    expect(document.activeElement).toBe(canvas)
    button.remove()
  })
})
