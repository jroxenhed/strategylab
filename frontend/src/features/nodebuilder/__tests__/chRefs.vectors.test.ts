/**
 * `ch()` scoping (F435 W7, plan "ch() scoping rules", critic 11).
 *
 * Two parts:
 * - the plan's rules, checked on a hand-built graph with a subnet (always runs);
 * - the shared vectors in backend/tests/nodebuilder/vectors/ch_refs.json,
 *   written by item 7.B and run by pytest too (test_ch_refs.py): `resolve`
 *   against resolveChPath, `rename` against paths.renameNode (whose ch()
 *   rewrite is the twin of migrate.rewrite_ch_paths). Nothing is skipped:
 *   a missing or reshaped file fails here.
 */

import { describe, it, expect } from 'vitest'
import type { Graph, GraphNode } from '../../../api/nodebuilder'
import vectorFile from '../../../../../backend/tests/nodebuilder/vectors/ch_refs.json'
import { resolveChPath } from '../code/chPaths'
import { chPathItems } from '../code/completion'
import { renameNode } from '../paths'

function node(id: string, type: string, name: string, parent: string | null, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name, parent, params: {}, position: [0, 0], display: false, bypass: false, ...extra }
}

/**
 * root: aapl, vol (wrangle), rsi, sub (subnet, promoted `lookback`)
 * sub:  sma, inner (wrangle)
 * root: shared (subnet) > spread
 */
const graph: Pick<Graph, 'nodes'> = {
  nodes: {
    aapl: node('aapl', 'ticker', 'aapl', null),
    vol: node('vol', 'wrangle', 'vol', null, { spare_params: [{ name: 'threshold', type: 'float', default: 2, label: 'threshold' }] }),
    rsi: node('rsi', 'rsi', 'rsi', null),
    sub: node('sub', 'subnet', 'sub', null, { promoted: [{ name: 'lookback', label: 'Lookback', target: 'sma/period', type: 'int', default: 50 }] }),
    sma: node('sma', 'sma', 'sma', 'sub'),
    inner: node('inner', 'wrangle', 'inner', 'sub'),
    shared: node('shared', 'subnet', 'shared', null),
    spread: node('spread', 'wrangle', 'spread', 'shared'),
  },
}

describe('ch() scoping rules (plan W7)', () => {
  it('a bare name is the calling node’s own param', () => {
    expect(resolveChPath(graph, 'vol', 'threshold')).toEqual({ node_id: 'vol', target: 'threshold' })
  })

  it('../other/param is a sibling’s param', () => {
    expect(resolveChPath(graph, 'rsi', '../vol/threshold')).toEqual({ node_id: 'vol', target: 'threshold' })
  })

  it('../other/@attr is a sibling’s output attribute', () => {
    expect(resolveChPath(graph, 'rsi', '../vol/@atr_pct')).toEqual({ node_id: 'vol', target: '@atr_pct' })
  })

  it('../name from a subnet child is a param of the enclosing network (a promoted param)', () => {
    expect(resolveChPath(graph, 'inner', '../lookback')).toEqual({ node_id: 'sub', target: 'lookback' })
  })

  it('a sibling inside a subnet is found inside it, not at the root', () => {
    expect(resolveChPath(graph, 'inner', '../sma/period')).toEqual({ node_id: 'sma', target: 'period' })
    expect(resolveChPath(graph, 'inner', '../rsi/period')).toBeNull()
  })

  it('absolute paths start at the root', () => {
    expect(resolveChPath(graph, 'rsi', '/shared/spread/@spread')).toEqual({ node_id: 'spread', target: '@spread' })
    expect(resolveChPath(graph, 'inner', '/vol/threshold')).toEqual({ node_id: 'vol', target: 'threshold' })
  })

  it('a path to a missing node, or above the root, leads nowhere', () => {
    expect(resolveChPath(graph, 'rsi', '../nope/x')).toBeNull()
    expect(resolveChPath(graph, 'rsi', '../threshold')).toBeNull()
    expect(resolveChPath(graph, 'rsi', '/threshold')).toBeNull()
  })

  it('completion offers sibling, param, promoted and root paths that all resolve', () => {
    const items = chPathItems(graph, 'inner', {})
    expect(items).toContain('../sma/')
    expect(items).toContain('../lookback')
    expect(items).toContain('/')
    for (const p of items.filter(i => !i.endsWith('/'))) {
      expect(resolveChPath(graph, 'inner', p), p).not.toBeNull()
    }
  })
})

/** The file's shape (see its `_doc`). */
interface ResolveVector {
  name: string
  from: string
  path: string
  expect: { node_id: string; target: string } | null
}
interface RenameVector {
  name: string
  rename: { node_id: string; new_name: string }
  node_id: string
  field: string
  before: string
  after: string
}

const file = vectorFile as unknown as { graph: Graph; resolve: ResolveVector[]; rename: RenameVector[] }

/** The text of `field` ('code' or 'params.<name>', an expression) on a node. */
function fieldText(g: Graph, nodeId: string, field: string): unknown {
  const n = g.nodes[nodeId]
  if (field === 'code') return n.code
  const name = field.startsWith('params.') ? field.slice('params.'.length) : null
  const v = name === null ? undefined : (n.params as Record<string, unknown>)[name]
  return v && typeof v === 'object' && 'expr' in v ? (v as { expr: unknown }).expr : v
}

describe('ch_refs.json shared vectors', () => {
  it('has a graph, resolve vectors and rename vectors', () => {
    expect(Object.keys(file.graph.nodes).length).toBeGreaterThan(0)
    expect(file.resolve.length).toBeGreaterThan(0)
    expect(file.rename.length).toBeGreaterThan(0)
  })

  it.each(file.resolve)('resolve: $name', v => {
    expect(resolveChPath(file.graph, v.from, v.path)).toEqual(v.expect)
  })

  it.each(file.rename)('rename: $name', v => {
    // The vector's `before` is what the shared graph holds now.
    expect(fieldText(file.graph, v.node_id, v.field)).toBe(v.before)
    const next = renameNode(file.graph, v.rename.node_id, v.rename.new_name)
    expect(fieldText(next, v.node_id, v.field)).toBe(v.after)
    expect(next.nodes[v.rename.node_id].name).toBe(v.rename.new_name)
    // The input graph is not changed.
    expect(fieldText(file.graph, v.node_id, v.field)).toBe(v.before)
  })
})
