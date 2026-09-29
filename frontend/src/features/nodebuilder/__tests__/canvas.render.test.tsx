/**
 * Canvas render smoke (F435 Wave 0 item 0.C): the editable canvas mounts
 * without a render loop, the Entry/Exit nodes use the 'nbOutput' React Flow
 * type (not React Flow's white default 'output'), and Tab / Delete reach the
 * canvas when focus is on the page body.
 */

import { describe, it, expect, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import Canvas from '../Canvas'
import { useNodeBuilderStore } from '../store'
import type { Graph } from '../../../api/nodebuilder'

function makeGraph(): Graph {
  return {
    nodes: {
      t: { id: 't', type: 'ticker', params: { symbol: 'AAPL', interval: '1d', source: 'yahoo' }, position: [0, 0], display: false, bypass: false },
      r: { id: 'r', type: 'rsi', params: { period: 14 }, position: [0, 150], display: false, bypass: false },
      e: { id: 'e', type: 'entry', params: {}, position: [0, 300], display: false, bypass: false },
    },
    wires: [
      { id: 'w1', from: 't', to: 'r', attr: '@close' },
      { id: 'w2', from: 'r', to: 'e', attr: '@rsi' },
    ],
    readOnly: false,
  } as unknown as Graph
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
  useNodeBuilderStore.setState({ graph: null, selectedNodeId: null })
})

function mount() {
  const g = makeGraph()
  useNodeBuilderStore.setState({ graph: g, selectedNodeId: null })
  const utils = render(
    <div className="nodebuilder-root" style={{ width: 800, height: 600 }}>
      <Canvas graph={g} />
    </div>,
  )
  // jsdom has no layout, so give the canvas a client rect to count as "in view".
  const canvas = utils.container.querySelector('.nodebuilder-root .nodebuilder-root') as HTMLElement
  canvas.getClientRects = () => ({ length: 1 }) as unknown as DOMRectList
  return { ...utils, canvas }
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
})
