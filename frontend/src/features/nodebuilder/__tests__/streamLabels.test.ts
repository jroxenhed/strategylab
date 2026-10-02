/**
 * Streams, wire labels, label placement, wire diagnostics and attribute
 * rename (F435 W2 item 2.E, specs S09, S10, S11; foundation 5.2).
 *
 * Pure functions from streamLabels.ts and renameAttr.ts, plus one store
 * check that a rename is a single undo step.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { emptyGraph, type AttrInfo, type Graph, type GraphNode, type GraphWire, type StreamSchema } from '../../../api/nodebuilder'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import {
  attrListValue,
  inputPortTitle,
  inputStreamOf,
  labelText,
  outputPortTitle,
  placeLabels,
  pointOnWire,
  readsOf,
  readsThroughWire,
  removeWiresWithTerms,
  staticInputNames,
  wireDiagnostics,
  wireIdForDiagnostic,
  wireLabels,
  wirePath,
  writeClashFor,
  writesOf,
  type LabelWire,
  type Rect,
} from '../streamLabels'
import { renameAttr } from '../renameAttr'
import { useNodeBuilderStore } from '../store'

vi.mock('../catalog.generated', async orig => {
  const real = await orig<typeof import('../catalog.generated')>()
  const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
  return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
})

function node(id: string, type: string, params: GraphNode['params'] = {}, position: [number, number] = [0, 0]): GraphNode {
  return { id, type, name: id, parent: null, params, position, display: false, bypass: false }
}
function wire(id: string, from: string, to: string, to_port = 'in0'): GraphWire {
  return { id, from, to, from_port: 'out', to_port }
}
function graphOf(nodes: GraphNode[], wires: GraphWire[] = []): Graph {
  return { ...emptyGraph(), nodes: Object.fromEntries(nodes.map(n => [n.id, n])), wires }
}
function attr(name: string, written_by: string, dtype: AttrInfo['dtype'] = 'float'): AttrInfo {
  return { name, dtype, written_by }
}
function stream(points: AttrInfo[], detail: AttrInfo[] = []): StreamSchema {
  return { stream_schema: 1, points, detail, prims: [] }
}

const OHLCV = ['@open', '@high', '@low', '@close', '@volume'].map(n => attr(n, 'aapl'))

// aapl -> rsi -> xb (a = @rsi), the S10 rename example.
function rsiChain(): Graph {
  return graphOf(
    [
      node('aapl', 'ticker', { symbol: 'AAPL', interval: '1d' }),
      node('rsi', 't2_ind', { period: 14, source: '@close', out: '@rsi' }),
      node('xb', 't2_cmp', { a: '@rsi', b: null, out: '@xb' }),
    ],
    [wire('w1', 'aapl', 'rsi'), wire('w2', 'rsi', 'xb')],
  )
}

describe('labelText', () => {
  it('shows one, two, or two plus a count', () => {
    expect(labelText(['@rsi'])).toBe('@rsi')
    expect(labelText(['@a', '@b'])).toBe('@a, @b')
    expect(labelText(['@a', '@b', '@c'])).toBe('@a, @b +1')
    expect(labelText(['@a', '@b', '@c', '@d', '@e'])).toBe('@a, @b +3')
    expect(labelText([])).toBe('')
  })
})

describe('reads, writes and wire labels', () => {
  it('reads come from attr params in param order; lists are flattened', () => {
    expect(readsOf(node('x', 't2_cmp', { a: '@rsi', b: '@sma' }))).toEqual(['@rsi', '@sma'])
    expect(readsOf(node('a', 't2_and', { terms: ['@p', '@q'] }))).toEqual(['@p', '@q'])
    expect(attrListValue('@p, @q')).toEqual(['@p', '@q'])
  })

  it('writes come from write params, else the fixed catalog writes', () => {
    expect(writesOf(node('m', 't2_macd', { out_line: '@m' })).map(w => w.name)).toEqual(['@m', '@macd_signal', '@macd_hist'])
    expect(writesOf(node('t', 'ticker')).map(w => [w.param, w.name])[0]).toEqual([null, '@open'])
  })

  it('a wire label is what the consumer reads through it', () => {
    const g = rsiChain()
    const streams = { aapl: stream(OHLCV), rsi: stream([...OHLCV, attr('@rsi', 'rsi')]) }
    expect(readsThroughWire(g.wires[1], g, streams)).toEqual(['@rsi'])
    expect(readsThroughWire(g.wires[0], g, streams)).toEqual(['@close'])
    const labels = wireLabels(g, streams)
    expect(labels.w2).toEqual({ reads: ['@rsi'], text: '@rsi', placeholder: false })
  })

  it('shows the stream placeholder when the consumer reads nothing yet', () => {
    const g = rsiChain()
    g.nodes.xb = { ...g.nodes.xb, params: { a: null, b: null, out: '@xb' } }
    expect(wireLabels(g, {}).w2).toEqual({ reads: [], text: 'stream', placeholder: true })
  })

  it('before /validate answers, labels fall back to what upstream writes', () => {
    expect(wireLabels(rsiChain(), {}).w2.text).toBe('@rsi')
  })

  it('a consumer with params but no read params reads nothing through its wire (FP-9)', () => {
    // merge passes the whole stream: a stale v2 `attr` on its wire is not a read.
    const g = graphOf([node('t', 'ticker'), node('m', 'merge')], [{ ...wire('w', 't', 'm'), attr: '@close' }])
    expect(wireLabels(g, {}).w).toEqual({ reads: [], text: 'stream', placeholder: true })
  })

  it('with streams from before a rename, the label follows the rename on the same render (FP-3)', () => {
    // The last /validate answered the graph before the rename: rsi still writes @rsi.
    const old: Record<string, StreamSchema> = {
      aapl: stream(OHLCV),
      rsi: stream([...OHLCV, attr('@rsi', 'rsi')]),
    }
    const next = renameAttr(rsiChain(), 'rsi', 'out', '@rsi14')
    // Trusting the stale streams alone drops the read (the old flash).
    expect(wireLabels(next, old, true).w2.text).toBe('stream')
    // Stale (fresh = false): the static guess counts too.
    expect(wireLabels(next, old, false).w2).toEqual({ reads: ['@rsi14'], text: '@rsi14', placeholder: false })
    // Fresh streams are still the only source.
    expect(readsThroughWire(next.wires[1], next, old)).toEqual([])
  })

  it('staticInputNames is the union of what flows into a node', () => {
    const names = staticInputNames(rsiChain(), 'xb')
    expect(names.has('@rsi')).toBe(true)
    expect(names.has('@xb')).toBe(false)
  })

  it('port hover texts', () => {
    expect(inputPortTitle('in1', ['@spread_z'])).toBe('in1 · @spread_z')
    expect(inputPortTitle('in1', [])).toBe('in1')
    expect(outputPortTitle([{ param: 'out', name: '@rsi' }, { param: 's', name: '@rsi_slope' }])).toBe('out · +@rsi +@rsi_slope')
  })
})

describe('inputStreamOf', () => {
  it('is the union of the wired streams, in port order', () => {
    const g = graphOf(
      [node('aapl', 'ticker'), node('rsi', 't2_ind'), node('x', 't2_cmp')],
      [wire('w2', 'rsi', 'x', 'in1'), wire('w1', 'aapl', 'x', 'in0')],
    )
    const streams = { aapl: stream(OHLCV), rsi: stream([...OHLCV, attr('@rsi', 'rsi')], [attr('@stop_pct', 'sl')]) }
    const input = inputStreamOf('x', streams, g.wires)
    expect(input.known).toBe(true)
    // A diamond from one Ticker lists each name once.
    expect(input.attrs.map(a => a.name)).toEqual(['@open', '@high', '@low', '@close', '@volume', '@rsi', '@stop_pct'])
    expect(input.attrs.find(a => a.name === '@stop_pct')?.detail).toBe(true)
    expect(input.attrs.find(a => a.name === '@rsi')?.port).toBe('in1')
  })

  it('lists a clash (same name, two writers) twice so it is visible', () => {
    const streams = { p: stream([attr('@rsi', 'r1')]), q: stream([attr('@rsi', 'r2')]) }
    const input = inputStreamOf('x', streams, [wire('a', 'p', 'x', 'in0'), wire('b', 'q', 'x', 'in1')])
    expect(input.attrs.map(a => a.written_by)).toEqual(['r1', 'r2'])
  })

  it('is not known while an upstream stream is missing, and not wired with no wires', () => {
    expect(inputStreamOf('x', { p: stream([]) }, [wire('a', 'p', 'x'), wire('b', 'q', 'x', 'in1')]).known).toBe(false)
    expect(inputStreamOf('x', {}, [])).toEqual({ attrs: [], wired: false, known: false })
  })
})

describe('placeLabels (foundation 5.2)', () => {
  // Source on top, three targets spread out far below, so nothing overlaps.
  const rects: Record<string, Rect> = {
    s: { x: 400, y: 0, w: 176, h: 60 },
    t1: { x: 0, y: 600, w: 176, h: 60 },
    t2: { x: 400, y: 600, w: 176, h: 60 },
    t3: { x: 800, y: 600, w: 176, h: 60 },
  }
  const fan = (texts: string[]): LabelWire[] => texts.map((text, i) => ({
    id: `w${i + 1}`, from: 's', to: `t${i + 1}`, toPort: 'in0', text,
  }))

  it('a fan-out of three same-text wires shows one label, on the leftmost', () => {
    const p = placeLabels(fan(['@rsi', '@rsi', '@rsi']), rects)
    expect(p.w1).toEqual({ t: 0.5, dx: 0, hidden: null })
    expect(p.w2.hidden).toBe('dup')
    expect(p.w3.hidden).toBe('dup')
  })

  it('a fan-out with different texts spreads them out', () => {
    const p = placeLabels(fan(['@a', '@b', '@c']), rects)
    expect([p.w1.t, p.w2.t, p.w3.t]).toEqual([0.38, 0.5, 0.62])
    expect([p.w1.dx, p.w2.dx, p.w3.dx]).toEqual([-10, 0, 10])
    expect([p.w1.hidden, p.w2.hidden, p.w3.hidden]).toEqual([null, null, null])
  })

  it('sorts a fan-out by target x, not by wire order', () => {
    const wires: LabelWire[] = [
      { id: 'right', from: 's', to: 't3', toPort: 'in0', text: '@x' },
      { id: 'left', from: 's', to: 't1', toPort: 'in0', text: '@x' },
    ]
    const p = placeLabels(wires, rects)
    expect(p.left.hidden).toBeNull()
    expect(p.right.hidden).toBe('dup')
  })

  it('fan-in of three wires at a dynamic node alternates t', () => {
    const r: Record<string, Rect> = {
      a: { x: 0, y: 0, w: 176, h: 60 },
      b: { x: 400, y: 0, w: 176, h: 60 },
      c: { x: 800, y: 0, w: 176, h: 60 },
      n: { x: 400, y: 700, w: 176, h: 60 },
    }
    const wires: LabelWire[] = ['a', 'b', 'c'].map((from, i) => ({ id: from, from, to: 'n', toPort: `in${i}`, text: `@${from}` }))
    const p = placeLabels(wires, r, { n: 4 }, new Set(['n']))
    expect([p.a.t, p.b.t, p.c.t]).toEqual([0.45, 0.6, 0.45])
  })

  it('moves a label off a node card, then hides it when there is no room', () => {
    // A node sits right on the wire's midpoint: the label moves along.
    const r: Record<string, Rect> = {
      s: { x: 0, y: 0, w: 176, h: 60 },
      t: { x: 0, y: 400, w: 176, h: 60 },
      block: { x: 60, y: 220, w: 60, h: 20 },
    }
    const p = placeLabels([{ id: 'w', from: 's', to: 't', toPort: 'in0', text: '@rsi' }], r, { t: 1 })
    expect(p.w.hidden).toBeNull()
    expect(p.w.t).toBeGreaterThan(0.5)
    // A node covering the whole wire leaves no room.
    const full = { ...r, block: { x: 0, y: 50, w: 176, h: 360 } }
    expect(placeLabels([{ id: 'w', from: 's', to: 't', toPort: 'in0', text: '@rsi' }], full, { t: 1 }).w.hidden).toBe('overlap')
  })
})

describe('wire geometry (foundation 5)', () => {
  it('leaves the output straight down and enters the input straight down', () => {
    expect(wirePath(0, 0, 100, 200)).toBe('M0,0 C0,90 100,110 100,200')
    expect(pointOnWire(0, 0, 100, 200, 0)).toEqual({ x: 0, y: 0 })
    expect(pointOnWire(0, 0, 100, 200, 1)).toEqual({ x: 100, y: 200 })
  })

  it('uses a long control distance for a wire that runs upward', () => {
    expect(wirePath(0, 200, 0, 0)).toBe('M0,200 C0,320 0,-120 0,0')
  })
})

describe('wire diagnostics (spec S05/S11)', () => {
  const d = (p: Partial<Diagnostic>): Diagnostic => ({
    node_id: null, path: null, severity: 'error', code: 'attr_missing', message: 'm', param: null, port: null,
    line: null, col: null, end_line: null, end_col: null, ...p,
  })

  it('a diagnostic with a port names the wire into that port', () => {
    expect(wireIdForDiagnostic(d({ node_id: 'xb', port: 'in0' }), rsiChain())).toBe('w2')
    expect(wireIdForDiagnostic(d({ node_id: 'xb', port: 'in1' }), rsiChain())).toBeNull()
  })

  it('dangling_wire names the wire whose other end is gone', () => {
    const g = rsiChain()
    g.wires.push(wire('wd', 'gone', 'xb', 'in1'))
    expect(wireIdForDiagnostic(d({ code: 'dangling_wire', node_id: 'xb' }), g)).toBe('wd')
  })

  it('an explicit-param stream problem marks the wire into that param on a two-input node (FP-7)', () => {
    const g = rsiChain()
    g.nodes.xb = { ...g.nodes.xb, params: { a: '@rsi', b: '@foo', out: '@xb' } }
    g.wires.push(wire('w3', 'aapl', 'xb', 'in1'))
    // The server sends param but no port for an explicitly set read.
    const map = wireDiagnostics([d({ node_id: 'xb', param: 'b', message: '@foo is missing' })], g)
    expect([...map.keys()]).toEqual(['w3'])
    const clash = wireDiagnostics([d({ node_id: 'xb', code: 'attr_clash', param: 'a' })], g)
    expect([...clash.keys()]).toEqual(['w2'])
    // No param and two wires: no guess.
    expect(wireDiagnostics([d({ node_id: 'xb' })], g).size).toBe(0)
  })

  it('a stream problem with no port marks the only wire into its node', () => {
    const map = wireDiagnostics([d({ node_id: 'xb', message: '@rsi is missing' }), d({ node_id: 'rsi', code: 'param_invalid' })], rsiChain())
    expect([...map.keys()]).toEqual(['w2'])
    expect(map.get('w2')?.message).toBe('@rsi is missing')
  })
})

describe('removeWiresWithTerms (FP-8)', () => {
  // xa and xb write @xa / @xb; all = AND over them (terms filled by connectWire).
  function andGraph(): Graph {
    return graphOf(
      [
        node('aapl', 'ticker'),
        node('xa', 't2_cmp', { a: '@close', out: '@xa' }),
        node('xb', 't2_cmp', { a: '@close', out: '@xb' }),
        node('all', 't2_and', { terms: ['@xa', '@xb'], out: '@all' }),
      ],
      [wire('w0', 'aapl', 'xa'), wire('w1', 'aapl', 'xb'), wire('wa', 'xa', 'all', 'in0'), wire('wb', 'xb', 'all', 'in1')],
    )
  }

  it('deleting a wire into AND takes back the term it added', () => {
    const next = removeWiresWithTerms(andGraph(), ['wb'])
    expect(next.wires.map(w => w.id)).toEqual(['w0', 'w1', 'wa'])
    expect(next.nodes.all.params.terms).toEqual(['@xa'])
  })

  it('keeps a term another remaining wire still provides', () => {
    const g = andGraph()
    // xb's output also reaches `all` through xa (xb -> xa -> all).
    g.wires.push(wire('wx', 'xb', 'xa', 'in1'))
    const next = removeWiresWithTerms(g, ['wb'])
    expect(next.nodes.all.params.terms).toEqual(['@xa', '@xb'])
  })

  it('leaves single attr params and other nodes alone', () => {
    const g = rsiChain()
    const next = removeWiresWithTerms(g, ['w2'])
    expect(next.nodes.xb.params.a).toBe('@rsi')
    expect(next.nodes).toBe(g.nodes)
    expect(removeWiresWithTerms(g, [])).toBe(g)
  })
})

describe('writeClashFor (FP-6)', () => {
  const clash = (node_id: string, param: string): Diagnostic => ({
    node_id, path: null, severity: 'error', code: 'attr_clash', message: `${node_id} reads a clashing name`,
    param, port: null, line: null, col: null, end_line: null, end_col: null,
  })
  // r1 and r2 both write @rsi; m merges them; xb reads a = @rsi.
  function clashGraph(): Graph {
    return graphOf(
      [
        node('aapl', 'ticker'),
        node('r1', 't2_ind', { source: '@close', out: '@rsi' }),
        node('r2', 't2_ind', { source: '@close', out: '@rsi' }),
        node('m', 'merge'),
        node('xb', 't2_cmp', { a: '@rsi', out: '@xb' }),
        node('other', 't2_ind', { source: '@close', out: '@rsi' }),
      ],
      [
        wire('w1', 'aapl', 'r1'), wire('w2', 'aapl', 'r2'), wire('w5', 'aapl', 'other'),
        wire('w3', 'r1', 'm', 'in0'), wire('w4', 'r2', 'm', 'in1'), wire('w6', 'm', 'xb'),
      ],
    )
  }

  it('finds the reader clash for each writer upstream of the reader', () => {
    const d = clash('xb', 'a')
    expect(writeClashFor(clashGraph(), [d], 'r1', '@rsi')).toBe(d)
    expect(writeClashFor(clashGraph(), [d], 'r2', '@rsi')).toBe(d)
  })

  it('not for a writer that does not reach the reader, or another name', () => {
    const d = clash('xb', 'a')
    expect(writeClashFor(clashGraph(), [d], 'other', '@rsi')).toBeNull()
    expect(writeClashFor(clashGraph(), [d], 'xb', '@xb')).toBeNull()
    expect(writeClashFor(clashGraph(), [], 'r1', '@rsi')).toBeNull()
  })
})

describe('renameAttr (spec S10)', () => {
  it('renames the write and every downstream reader', () => {
    const next = renameAttr(rsiChain(), 'rsi', 'out', '@rsi14')
    expect(next.nodes.rsi.params.out).toBe('@rsi14')
    expect(next.nodes.xb.params.a).toBe('@rsi14')
    expect(wireLabels(next, {}).w2.text).toBe('@rsi14')
  })

  it('stops at a node that writes the old name again', () => {
    const g = rsiChain()
    g.nodes.mid = node('mid', 't2_ind', { source: '@rsi', out: '@rsi' })
    g.nodes.y = node('y', 't2_cmp', { a: '@rsi', out: '@y' })
    g.wires.push(wire('w3', 'rsi', 'mid'), wire('w4', 'mid', 'y'))
    const next = renameAttr(g, 'rsi', 'out', '@r')
    expect(next.nodes.mid.params.source).toBe('@r')
    expect(next.nodes.mid.params.out).toBe('@rsi')
    expect(next.nodes.y.params.a).toBe('@rsi')
  })

  it('renames inside attr lists and leaves upstream nodes alone', () => {
    const g = rsiChain()
    g.nodes.all = node('all', 't2_and', { terms: ['@xb', '@close'], out: '@all' })
    g.wires.push(wire('w3', 'xb', 'all'))
    const next = renameAttr(g, 'xb', 'out', '@cross')
    expect(next.nodes.all.params.terms).toEqual(['@cross', '@close'])
    expect(next.nodes.rsi).toBe(g.nodes.rsi)
  })

  it('a rename to the same name changes nothing', () => {
    const g = rsiChain()
    expect(renameAttr(g, 'rsi', 'out', '@rsi')).toBe(g)
  })

  describe('in the store', () => {
    beforeEach(() => {
      useNodeBuilderStore.getState().openGraph(rsiChain(), { id: null, rev: 0, name: 'test' })
    })

    it('is one undo step, and undo restores the writer and the reader', () => {
      const s = useNodeBuilderStore.getState
      const before = s().past.length
      s().commit('rename @rsi to @rsi14', g => renameAttr(g, 'rsi', 'out', '@rsi14'))
      expect(s().past.length).toBe(before + 1)
      expect(s().graph!.nodes.xb.params.a).toBe('@rsi14')
      s().undo()
      expect(s().graph!.nodes.rsi.params.out).toBe('@rsi')
      expect(s().graph!.nodes.xb.params.a).toBe('@rsi')
    })
  })
})
