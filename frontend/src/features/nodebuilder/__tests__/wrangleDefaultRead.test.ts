/**
 * A Wrangle with exactly one write uses it as its primary write (F435 W7,
 * the frontend mirror of the backend's `schema.primary_write`): a node
 * wired below it reads that write without naming it. With several writes
 * the reader must name one, so nothing is filled in.
 */

import { afterEach, describe, expect, it } from 'vitest'
import { emptyGraph, type Graph, type GraphNode, type StreamSchema } from '../../../api/nodebuilder'
import type { ParseCodeResponse } from '../../../api/nodebuilderCode'
import { CODE_SLOT, codeWritesOf, parseKey, resetCodeStore, useCodeStore } from '../code/codeStore'
import { connectWire, primaryWriteOf } from '../streamLabels'
import { resetDiagnostics, setStreams } from '../useDiagnostics'

const ONE = '@sig: bool = @close > 0\n'
const TWO = '@a = @close * 2\n@b = @close * 3\n'

function makeNode(id: string, type: string, extra: Partial<GraphNode> = {}): GraphNode {
  return { id, type, name: id, parent: null, params: {}, position: [0, 0], display: false, bypass: false, ...extra }
}

function wrangle(code: string | null, id = 'w'): GraphNode {
  return makeNode(id, 'wrangle', { code })
}

function parsed(code: string, writes: string[], id = 'w'): void {
  const res: ParseCodeResponse = {
    ok: true, params: [], reads: [], result_type: null, diagnostics: [],
    writes: writes.map(name => ({ name, class: 'point', dtype: 'any' })),
  }
  useCodeStore.setState({ parses: { [parseKey(id, CODE_SLOT)]: { code, res, at: 0 } } })
}

function stream(points: { name: string; written_by: string }[]): StreamSchema {
  return { stream_schema: 1, points: points.map(p => ({ ...p, dtype: 'any' })), detail: [] } as unknown as StreamSchema
}

afterEach(() => {
  resetCodeStore()
  resetDiagnostics()
})

describe('primaryWriteOf for a Wrangle', () => {
  it('hands on its one write', () => {
    expect(primaryWriteOf(wrangle(ONE), ['@sig'])).toBe('@sig')
  })

  it('asks the reader to name one when the code writes several', () => {
    expect(primaryWriteOf(wrangle(TWO), ['@a', '@b'])).toBeNull()
  })

  it('has none with no code or no known writes', () => {
    expect(primaryWriteOf(wrangle(null), ['@sig'])).toBeNull()
    expect(primaryWriteOf(wrangle(ONE))).toBeNull()   // nothing parsed or validated yet
  })

  it('leaves nodes with their own writes alone', () => {
    const rsi = makeNode('rsi', 'rsi', { code: '@x = 1\n' })
    expect(primaryWriteOf(rsi, ['@x'])).toBe('@rsi')
  })

  it('reads the writes from the parse of this very code', () => {
    parsed(ONE, ['@sig'])
    expect(codeWritesOf(wrangle(ONE))).toEqual(['@sig'])
    expect(primaryWriteOf(wrangle(ONE))).toBe('@sig')
    parsed(TWO, ['@a', '@b'])
    expect(primaryWriteOf(wrangle(TWO))).toBeNull()
  })

  it('falls back to the last validate when the parse is for older code', () => {
    parsed('@old = 1\n', ['@old'])
    setStreams({ w: stream([{ name: '@close', written_by: 't' }, { name: '@sig', written_by: 'w' }]) })
    expect(codeWritesOf(wrangle(ONE))).toEqual(['@sig'])
    expect(primaryWriteOf(wrangle(ONE))).toBe('@sig')
  })

  it('fills the default read of a node wired below it', () => {
    parsed(ONE, ['@sig'])
    const base = emptyGraph()
    const graph: Graph = {
      ...base,
      nodes: {
        w: wrangle(ONE),
        hi: makeNode('hi', 'above'),
      },
    }
    const next = connectWire(graph, { id: 'wire1', from: 'w', to: 'hi', to_port: 'in0' })
    expect(next.nodes.hi.params.a).toBe('@sig')
    parsed(TWO, ['@a', '@b'])
    const two = { ...graph, nodes: { ...graph.nodes, w: wrangle(TWO) } }
    expect(connectWire(two, { id: 'wire1', from: 'w', to: 'hi', to_port: 'in0' }).nodes.hi.params.a ?? null).toBeNull()
  })
})
