/**
 * Tidy layout (F435 W3 item 3.F): elk lays nodes out top-down, the result is
 * the same for the same graph, the selection mode moves only the selection,
 * the L command is one undo step, and "Edit this graph" tidies the copy
 * without making an undo step or marking it dirty.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { emptyGraph, type Graph, type GraphNode, type GraphWire } from '../../../api/nodebuilder'
import { applyPositions, estimateNodeSize, tidyGraph, tidyPositions } from '../layout'
import { useNodeBuilderStore } from '../store'
import { getCommand, listCommands, runCommand } from '../commands'

const s = () => useNodeBuilderStore.getState()

function node(id: string, type: string, position: [number, number], params: GraphNode['params'] = {}): GraphNode {
  return { id, type, name: id, parent: null, params, position, display: false, bypass: false }
}

function wire(id: string, from: string, to: string, to_port = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port }
}

/** ticker -> rsi -> a, ticker -> sma -> b of crosses_above -> entry, all piled up. */
function strategy(): Graph {
  const g = emptyGraph()
  g.nodes = {
    t: node('t', 'ticker', [0, 0], { symbol: 'AAPL', interval: '1d' }),
    // Drawn the wrong way round: sma left of rsi, though rsi feeds input a.
    sma: node('sma', 'sma', [0, 10], { period: 20 }),
    rsi: node('rsi', 'rsi', [300, 10], { period: 14 }),
    x: node('x', 'crosses_above', [100, 20]),
    e: node('e', 'entry', [100, 30]),
  }
  g.wires = [
    wire('w1', 't', 'rsi'),
    wire('w2', 't', 'sma'),
    wire('w3', 'rsi', 'x', 'in0'),
    wire('w4', 'sma', 'x', 'in1'),
    wire('w5', 'x', 'e'),
  ]
  return g
}

/** The same graph with its node keys and wires in reverse order. */
function shuffled(g: Graph): Graph {
  const nodes: Record<string, GraphNode> = {}
  for (const id of Object.keys(g.nodes).reverse()) nodes[id] = g.nodes[id]
  return { ...g, nodes, wires: [...g.wires].reverse() }
}

function rect(g: Graph, id: string) {
  const n = g.nodes[id]
  const size = estimateNodeSize(n)
  return { x: n.position[0], y: n.position[1], w: size.width, h: size.height }
}

describe('tidyPositions', () => {
  it('is deterministic for the same input, whatever the key order', async () => {
    const a = await tidyPositions(strategy())
    const b = await tidyPositions(strategy())
    const c = await tidyPositions(shuffled(strategy()))
    expect([...a.entries()].sort()).toEqual([...b.entries()].sort())
    expect([...a.entries()].sort()).toEqual([...c.entries()].sort())
    expect(a.size).toBe(5)
  })

  it('lays out top-down: each node sits below the nodes feeding it', async () => {
    const g = await tidyGraph(strategy())
    const y = (id: string) => g.nodes[id].position[1]
    expect(y('t')).toBeLessThan(y('rsi'))
    expect(y('t')).toBeLessThan(y('sma'))
    expect(y('rsi')).toBe(y('sma'))
    expect(y('rsi')).toBeLessThan(y('x'))
    expect(y('x')).toBeLessThan(y('e'))
  })

  it('keeps the input order: the node wired into input a ends up on the left', async () => {
    const g = await tidyGraph(strategy())
    expect(g.nodes.rsi.position[0]).toBeLessThan(g.nodes.sma.position[0])
  })

  it('leaves no two nodes overlapping', async () => {
    const g = await tidyGraph(strategy())
    const ids = Object.keys(g.nodes)
    for (const a of ids) {
      for (const b of ids) {
        if (a >= b) continue
        const r1 = rect(g, a)
        const r2 = rect(g, b)
        const overlap = r1.x < r2.x + r2.w && r2.x < r1.x + r1.w && r1.y < r2.y + r2.h && r2.y < r1.y + r1.h
        expect(overlap, `${a} overlaps ${b}`).toBe(false)
      }
    }
  })

  it('keeps the top-left corner of the nodes it lays out, on whole numbers', async () => {
    const g = strategy()
    for (const n of Object.values(g.nodes)) n.position = [n.position[0] + 500, n.position[1] + 250]
    const out = await tidyPositions(g)
    const xs = [...out.values()].map(p => p[0])
    const ys = [...out.values()].map(p => p[1])
    expect(Math.min(...xs)).toBe(500)
    expect(Math.min(...ys)).toBe(250)
    for (const [x, y] of out.values()) {
      expect(Number.isInteger(x)).toBe(true)
      expect(Number.isInteger(y)).toBe(true)
    }
  })

  it('with ids, lays out only those nodes', async () => {
    const out = await tidyPositions(strategy(), ['x', 'e'])
    expect([...out.keys()].sort()).toEqual(['e', 'x'])
  })

  it('lays out each network on its own', async () => {
    const g = strategy()
    g.nodes.n1 = { ...node('n1', 'ticker', [2000, 2000]), parent: 'net' }
    g.nodes.n2 = { ...node('n2', 'rsi', [2000, 2000]), parent: 'net' }
    g.wires.push(wire('w9', 'n1', 'n2'))
    const out = await tidyPositions(g)
    expect(out.get('n1')).toEqual([2000, 2000])
    expect(out.get('n2')![1]).toBeGreaterThan(2000)
    expect(Math.min(...['t', 'sma', 'rsi', 'x', 'e'].map(id => out.get(id)![1]))).toBe(0)
  })

  it('uses measured sizes when given', async () => {
    const tall = await tidyPositions(strategy(), null, () => ({ width: 176, height: 400 }))
    const small = await tidyPositions(strategy(), null)
    expect(tall.get('e')![1]).toBeGreaterThan(small.get('e')![1])
  })

  it('an empty graph gives no positions', async () => {
    expect((await tidyPositions(emptyGraph())).size).toBe(0)
  })
})

describe('applyPositions', () => {
  it('returns the same graph when nothing moves', () => {
    const g = strategy()
    expect(applyPositions(g, new Map([['t', [0, 0]]]))).toBe(g)
    expect(applyPositions(g, new Map([['gone', [5, 5]]]))).toBe(g)
  })

  it('moves only the given nodes and keeps the others as the same objects', () => {
    const g = strategy()
    const next = applyPositions(g, new Map([['t', [40, 80]]]))
    expect(next).not.toBe(g)
    expect(next.nodes.t.position).toEqual([40, 80])
    expect(next.nodes.rsi).toBe(g.nodes.rsi)
    expect(g.nodes.t.position).toEqual([0, 0])
  })
})

describe('L command', () => {
  beforeEach(() => {
    s().openGraph(strategy(), { id: null, rev: 0, name: 'test' })
  })

  it('is registered on L, in the pane menu, as layout.tidy', () => {
    const cmd = getCommand('layout.tidy')
    expect(cmd?.keys).toContain('l')
    expect(cmd?.menu).toBe('pane')
    expect(listCommands().some(c => c.id === 'layout.tidy')).toBe(true)
  })

  it('tidies every node as one undo step, and undo puts them back', async () => {
    const before = s().graph!
    expect(runCommand('layout.tidy', { canvas: null })).toBe(true)
    await expect.poll(() => s().graph !== before).toBe(true)
    expect(s().past).toHaveLength(1)
    expect(s().past[0].label).toBe('tidy layout')
    expect(s().dirty).toBe(true)
    expect(s().graph).toEqual(await tidyGraph(before))
    s().undo()
    expect(s().graph).toBe(before)
  })

  it('with a selection, moves only the selected nodes', async () => {
    const before = s().graph!
    s().setSelection({ nodeIds: ['x', 'e'] })
    runCommand('layout.tidy', { canvas: null })
    await expect.poll(() => s().graph !== before).toBe(true)
    const after = s().graph!
    for (const id of ['t', 'sma', 'rsi']) expect(after.nodes[id]).toBe(before.nodes[id])
    expect(after.nodes.e.position[1]).toBeGreaterThan(after.nodes.x.position[1])
    expect(s().past[0].label).toBe('tidy 2 nodes')
  })

  it('with one node selected, does nothing and says why', async () => {
    const before = s().graph!
    s().setSelection({ nodeIds: ['x'] })
    expect(runCommand('layout.tidy', { canvas: null })).toBe(true)
    await new Promise(r => setTimeout(r, 0))
    expect(s().graph).toBe(before)
    expect(s().flash?.text).toMatch(/two or more/)
  })

  it('does not commit when the graph changed while elk ran', async () => {
    runCommand('layout.tidy', { canvas: null })
    s().moveNode('t', [999, 999])   // lands before the async layout resolves
    const moved = s().graph
    await expect.poll(() => s().flash?.text ?? '').toMatch(/changed while tidying/)
    expect(s().graph).toBe(moved)
    expect(s().past).toHaveLength(1)
  })

  it('is disabled on a read-only graph or with nothing loaded', () => {
    s().discardEdits()
    expect(runCommand('layout.tidy', { canvas: null })).toBe(false)
    useNodeBuilderStore.setState({ graph: { ...strategy(), readOnly: true } })
    expect(runCommand('layout.tidy', { canvas: null })).toBe(false)
  })
})

describe('Edit this graph (loadFromAutoRender)', () => {
  beforeEach(() => {
    s().discardEdits()
  })

  it('tidies the copy with elk, as part of the load: no undo step, not dirty', async () => {
    const epoch = s().layoutEpoch
    s().loadFromAutoRender({ ...strategy(), readOnly: true })
    const loaded = s().graph!
    expect(s().layoutEpoch).toBe(epoch + 1)
    await expect.poll(() => s().graph !== loaded).toBe(true)
    const g = s().graph!
    expect(g.readOnly).toBe(false)
    expect(g.nodes.t.position[1]).toBeLessThan(g.nodes.rsi.position[1])
    expect(g.nodes.x.position[1]).toBeLessThan(g.nodes.e.position[1])
    expect(s().past).toHaveLength(0)
    expect(s().dirty).toBe(false)
    expect(s().savedGraph).toBe(g)
    // The canvas refits to the new positions.
    expect(s().layoutEpoch).toBe(epoch + 2)
  })

  it('is the same layout every time', async () => {
    s().loadFromAutoRender({ ...strategy(), readOnly: true })
    const first = s().graph
    await expect.poll(() => s().graph !== first).toBe(true)
    const a = s().graph
    s().loadFromAutoRender({ ...strategy(), readOnly: true })
    const second = s().graph
    await expect.poll(() => s().graph !== second).toBe(true)
    expect(s().graph).toEqual(a)
  })

  it('leaves the copy alone when the user edits before elk is done', async () => {
    s().loadFromAutoRender({ ...strategy(), readOnly: true })
    s().moveNode('t', [7, 7])
    const edited = s().graph
    await new Promise(r => setTimeout(r, 50))
    await tidyGraph(strategy())   // elk is loaded and has had its turn
    expect(s().graph).toBe(edited)
    expect(s().past).toHaveLength(1)
  })
})
