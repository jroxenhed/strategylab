/**
 * Node handle tests (F435 Wave 0, item 0.D, bug 15).
 *
 * Terminals (Entry/Exit/Size/Stop) have no output handle and the Ticker has
 * no input handle. Middle nodes keep both. Checked in edit mode and in the
 * read-only viewer, where handles are invisible but still in the DOM.
 */

import { describe, it, expect, afterEach, beforeAll } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { ReactFlow, type Node, type NodeTypes } from '@xyflow/react'
import TickerNode from '../nodes/TickerNode'
import OutputNode from '../nodes/OutputNode'
import IndicatorNode from '../nodes/IndicatorNode'
import SettingsNode from '../nodes/SettingsNode'

beforeAll(() => {
  // jsdom has no DOMMatrixReadOnly; React Flow reads it when measuring nodes.
  if (typeof (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly === 'undefined') {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
      constructor(_t?: string) {}
    }
  }
})

afterEach(() => cleanup())

const nodeTypes: NodeTypes = {
  ticker: TickerNode,
  nbOutput: OutputNode,
  indicator: IndicatorNode,
  settings: SettingsNode,
}

function node(id: string, type: string, backendType: string, editable: boolean): Node {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: {
      backendType,
      catalog: null,
      params: {},
      display: false,
      bypass: false,
      nodePath: `/${id}`,
      editable,
    },
  }
}

function handlesOf(container: HTMLElement, id: string) {
  const el = container.querySelector(`.react-flow__node[data-id="${id}"]`)
  if (!el) throw new Error(`node ${id} not rendered`)
  return {
    source: el.querySelectorAll('.react-flow__handle.source').length,
    target: el.querySelectorAll('.react-flow__handle.target').length,
  }
}

describe.each([true, false])('node handles (editable=%s)', editable => {
  function renderAll() {
    const nodes = [
      node('ticker', 'ticker', 'ticker', editable),
      node('entry', 'nbOutput', 'entry', editable),
      node('exit', 'nbOutput', 'exit', editable),
      node('rsi', 'indicator', 'rsi', editable),
      node('size', 'settings', 'position_size', editable),
    ]
    const { container } = render(
      <div style={{ width: 800, height: 600 }}>
        <ReactFlow nodes={nodes} edges={[]} nodeTypes={nodeTypes} />
      </div>,
    )
    return container
  }

  it('Ticker has an output handle and no input handle', () => {
    expect(handlesOf(renderAll(), 'ticker')).toEqual({ source: 1, target: 0 })
  })

  it('Entry and Exit have an input handle and no output handle', () => {
    const c = renderAll()
    expect(handlesOf(c, 'entry')).toEqual({ source: 0, target: 1 })
    expect(handlesOf(c, 'exit')).toEqual({ source: 0, target: 1 })
  })

  it('an indicator keeps both handles', () => {
    expect(handlesOf(renderAll(), 'rsi')).toEqual({ source: 1, target: 1 })
  })

  it('a settings node has no handles (a setting always applies)', () => {
    expect(handlesOf(renderAll(), 'size')).toEqual({ source: 0, target: 0 })
  })
})

describe('settings node labels in the read-only view', () => {
  function renderOne(backendType: string, params: Record<string, unknown>) {
    const n = node('s', 'settings', backendType, false)
    ;(n.data as { params: Record<string, unknown> }).params = params
    const { container } = render(
      <div style={{ width: 800, height: 600 }}>
        <ReactFlow nodes={[n]} edges={[]} nodeTypes={nodeTypes} />
      </div>,
    )
    return container.textContent ?? ''
  }

  it('shows a pct trailing stop with its value', () => {
    const text = renderOne('trailing_stop', { type: 'pct', value: 3 })
    expect(text).toContain('Trailing Stop')
    expect(text).toContain('3% trail')
  })

  it('shows an ATR trailing stop with activation', () => {
    const text = renderOne('trailing_stop', { type: 'atr', value: 2, activate_on_profit: true, activate_pct: 1.5 })
    expect(text).toContain('2x ATR trail after +1.5%')
  })

  it('tells long and short stops apart', () => {
    expect(renderOne('stop_loss', { pct: 3, direction: 'long' })).toContain('Stop Loss (long)')
    cleanup()
    expect(renderOne('stop_loss', { pct: 4, direction: 'short' })).toContain('Stop Loss (short)')
  })
})
