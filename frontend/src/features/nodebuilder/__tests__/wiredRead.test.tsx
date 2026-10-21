/**
 * An empty attr param that a wire feeds looks wired, not unset (F453 item 7).
 *
 * The G1 paper-bot graph was written through the API without the `a` of
 * Below/Above or the `signal` of Entry/Exit. The compiler falls back to the
 * wired node's main output (@rsi, @lo), and the bot runs, but the chips
 * showed the yellow "pick" warning. They now show "@rsi (wired)", muted.
 */

import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import { emptyGraph, type Graph, type GraphNode, type GraphWire } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { AttrPicker } from '../nodes/AttrPicker'
import { paramSpecsOf, wiredDefaultRead } from '../streamLabels'

function node(id: string, type: string, params: Record<string, unknown> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: params as GraphNode['params'], position: [0, 0], display: false, bypass: false }
}
function wire(id: string, from: string, to: string, to_port = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port }
}

/** The G1 paper-bot graph (.run/F453/g1-prod.json), as the API stored it. */
function g1(): Graph {
  return {
    ...emptyGraph(),
    nodes: {
      tick: node('tick', 'ticker', { symbol: 'SPY', interval: '5m' }),
      rsi: node('rsi', 'rsi', { period: 14 }),
      lo: node('lo', 'below', { threshold: 40, out: '@lo' }),
      hi: node('hi', 'above', { threshold: 60, out: '@hi' }),
      entry: node('entry', 'entry'),
      exit: node('exit', 'exit'),
      stop: node('stop', 'stop', { constant: 0.5 }),
    },
    wires: [
      wire('w1', 'tick', 'rsi'), wire('w2', 'rsi', 'lo'), wire('w3', 'rsi', 'hi'),
      wire('w4', 'lo', 'entry'), wire('w5', 'hi', 'exit'),
    ],
  }
}

function Picker({ nodeId, param }: { nodeId: string; param: string }) {
  const value = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.params[param])
  const type = useNodeBuilderStore(s => s.graph?.nodes[nodeId]?.type)
  const spec = paramSpecsOf(type).find(p => p.name === param)!
  return <AttrPicker nodeId={nodeId} spec={spec} value={value} />
}

afterEach(() => cleanup())

describe('wiredDefaultRead (F453)', () => {
  it('reads the wired node\'s main output, as the compiler does', () => {
    const g = g1()
    expect(wiredDefaultRead(g, 'lo', 'a')).toBe('@rsi')
    expect(wiredDefaultRead(g, 'hi', 'a')).toBe('@rsi')
    expect(wiredDefaultRead(g, 'entry', 'signal')).toBe('@lo')
    expect(wiredDefaultRead(g, 'exit', 'signal')).toBe('@hi')
  })

  it('null for a param with no wire on its port, or an unknown node', () => {
    const g = g1()
    // `b` of Below is its second port; nothing is wired there.
    expect(wiredDefaultRead(g, 'lo', 'b')).toBeNull()
    expect(wiredDefaultRead({ ...g, wires: [] }, 'lo', 'a')).toBeNull()
    expect(wiredDefaultRead(g, 'nope', 'a')).toBeNull()
  })
})

describe('the chips of the G1 graph (F453)', () => {
  it('Below `a` and Entry `signal` show the wired read, muted, not "pick"', () => {
    act(() => { useNodeBuilderStore.getState().openGraph(g1(), { id: 'g_8bb5dc94b330', rev: 1, name: 'G1' }) })
    render(<><Picker nodeId="lo" param="a" /><Picker nodeId="entry" param="signal" /></>)
    const a = screen.getByTestId('nb-attr-chip-lo-a')
    expect(a.textContent).toContain('@rsi (wired)')
    expect(a.className).toContain('nb-attr-chip--wired')
    expect(a.className).not.toContain('nb-attr-chip--required')
    const sig = screen.getByTestId('nb-attr-chip-entry-signal')
    expect(sig.textContent).toContain('@lo (wired)')
    expect(sig.className).toContain('nb-attr-chip--wired')
    // Showing the chip changes nothing in the graph.
    expect(useNodeBuilderStore.getState().dirty).toBe(false)
  })

  it('an Entry with nothing wired keeps the yellow "pick"', () => {
    const g = g1()
    act(() => { useNodeBuilderStore.getState().openGraph({ ...g, wires: g.wires.filter(w => w.id !== 'w4') }, { id: null, rev: 0, name: 'x' }) })
    render(<Picker nodeId="entry" param="signal" />)
    const sig = screen.getByTestId('nb-attr-chip-entry-signal')
    expect(sig.textContent).toContain('pick')
    expect(sig.className).toContain('nb-attr-chip--required')
  })
})
