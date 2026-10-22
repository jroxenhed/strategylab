/**
 * F457 (UX-3): a click on an input port that has a wire selects that wire,
 * so Delete removes it. A short wire between two close cards sits under the
 * ports' grab areas and is otherwise hard to hit. Driven with real DOM
 * clicks on stand-in handles (React Flow's handle classes and data attrs).
 */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode } from '../../../api/nodebuilder'
import type { CanvasCtx } from '../canvasPlugins'
import { setActiveCanvas } from '../screen'
import { useNodeBuilderStore } from '../store'
import { PortWireSelect, portWire } from '../plugins/portWireSelect'
import { shortcutGroups } from '../shortcutList'

function gnode(id: string, type: string, params: Record<string, unknown> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}

/** t → r (in0); s has a free in0. */
function makeGraph(): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      t: gnode('t', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      r: gnode('r', 'rsi', { period: 14, source: '@close', out: '@rsi' }),
      s: gnode('s', 'sma', { period: 20, source: '@close', out: '@sma' }),
    },
    wires: [{ id: 'w1', from: 't', to: 'r', from_port: 'out', to_port: 'in0' }],
  }
}

const st = () => useNodeBuilderStore.getState()

function handle(nodeId: string, id: string, kind: 'source' | 'target'): string {
  return `<div class="react-flow__handle ${kind}" data-nodeid="${nodeId}" data-handleid="${id}"><span class="dot"></span></div>`
}

function mountCanvas() {
  const root = document.createElement('div')
  root.innerHTML = `
    <div class="react-flow__node" data-id="r">${handle('r', 'in0', 'target')}${handle('r', 'out', 'source')}</div>
    <div class="react-flow__node" data-id="s">${handle('s', 'in0', 'target')}</div>`
  document.body.appendChild(root)
  const focus = vi.fn()
  const ctx = {
    store: useNodeBuilderStore,
    graph: () => st().graph!,
    editable: () => true,
    container: () => root,
    focus,
  } as unknown as CanvasCtx
  act(() => setActiveCanvas(ctx))
  const seen = vi.fn()
  root.addEventListener('click', seen)
  const click = (sel: string) => {
    const el = root.querySelector(sel)!
    act(() => { el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })) })
  }
  return { root, seen, click, focus }
}

beforeEach(() => {
  act(() => { st().openGraph(makeGraph(), { id: null, rev: 0, name: 'test' }) })
  render(<PortWireSelect />)
})

afterEach(() => {
  cleanup()
  act(() => setActiveCanvas(null))
  document.body.innerHTML = ''
  st().discardEdits()
})

describe('click on an occupied input port', () => {
  it('selects the wire into it, and the click goes no further', () => {
    const { seen, click, focus } = mountCanvas()
    click('[data-nodeid="r"][data-handleid="in0"] .dot')
    expect(st().selectedWireIds).toEqual(['w1'])
    expect(seen).not.toHaveBeenCalled()
    expect(focus).toHaveBeenCalled()
  })

  it('a free input or an output port is left to React Flow', () => {
    const { seen, click } = mountCanvas()
    click('[data-nodeid="s"][data-handleid="in0"]')
    click('[data-nodeid="r"][data-handleid="out"]')
    expect(st().selectedWireIds).toEqual([])
    expect(seen).toHaveBeenCalledTimes(2)
  })

  it('a click that finishes a click-to-connect is left alone', () => {
    const { root, seen, click } = mountCanvas()
    root.querySelector('[data-handleid="out"]')!.classList.add('clickconnecting')
    click('[data-nodeid="r"][data-handleid="in0"]')
    expect(st().selectedWireIds).toEqual([])
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it('portWire reads the wire on a frame port too', () => {
    const g: Graph = {
      ...emptyGraph(),
      nodes: {
        t: gnode('t', 'ticker', { symbol: 'AAPL', interval: '1d' }),
        grp: gnode('grp', 'subnet', {}),
        so: { ...gnode('so', 'subnet_output', {}), parent: 'grp' },
        rin: { ...gnode('rin', 'rsi', { period: 14, out: '@rsi' }), parent: 'grp' },
      },
      wires: [
        { id: 'w_t_grp', from: 't', to: 'grp', from_port: 'out', to_port: 'in0' },
        { id: 'w_rin_so', from: 'rin', to: 'so', from_port: 'out', to_port: 'in0' },
      ],
    }
    const el = document.createElement('div')
    el.className = 'react-flow__handle target'
    el.setAttribute('data-nodeid', 'grp')
    el.setAttribute('data-handleid', 'in0')
    expect(portWire(g, el)).toBe('w_t_grp')
    el.setAttribute('data-handleid', 'bnd:so')
    expect(portWire(g, el)).toBe('w_rin_so')
  })
})

describe('the ? overlay', () => {
  it('lists selecting and replacing a wire', () => {
    const rows = (q: string) => shortcutGroups(q, []).find(g => g.key === 'mouse')?.rows.map(r => [r.label, r.caps[0]])
    expect(rows('select wire')).toEqual([['Select wire', 'click its input port']])
    expect(rows('replace wire')).toEqual([['Replace wire', 'drop on a wired input']])
  })
})
