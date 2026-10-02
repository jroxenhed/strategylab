// Runs the shared path vectors (backend/tests/nodebuilder/vectors/paths.json).
// pytest runs the same file against backend/nodebuilder/migrate.py, so both
// languages agree on names, paths and renames.
import { describe, expect, it } from 'vitest'
import vectors from '../../../../../backend/tests/nodebuilder/vectors/paths.json'
import {
  PathError,
  defaultName,
  findByPath,
  isValidName,
  nodePath,
  renameNode,
  sanitizeName,
  uniqueName,
  type PathGraph,
} from '../paths'

const graphs = vectors.graphs as unknown as Record<string, PathGraph>

describe('paths vectors: names', () => {
  it.each(vectors.is_valid_name)('isValidName($input)', ({ input, expect: want }) => {
    expect(isValidName(input)).toBe(want)
  })
  it.each(vectors.sanitize_name)('sanitizeName($input)', ({ input, expect: want }) => {
    expect(sanitizeName(input)).toBe(want)
    expect(isValidName(want)).toBe(true)
  })
  it.each(vectors.default_name)('defaultName($id, $type)', ({ id, type, expect: want }) => {
    expect(defaultName(id, type)).toBe(want)
  })
  it.each(vectors.unique_name)('uniqueName($base)', ({ base, taken, expect: want }) => {
    expect(uniqueName(base, taken)).toBe(want)
  })
})

describe('paths vectors: paths', () => {
  it.each(vectors.node_path)('nodePath($id)', ({ graph, id, expect: want }) => {
    expect(nodePath(graphs[graph], id)).toBe(want)
  })
  it.each(vectors.find_by_path)('findByPath: $why', ({ graph, path, relative_to, expect: want }) => {
    expect(findByPath(graphs[graph], path, relative_to)).toBe(want)
  })
})

describe('paths vectors: renameNode', () => {
  it.each(vectors.rename_node)('$why', (c) => {
    const g = graphs[c.graph]
    const before = JSON.stringify(g)
    const want = c.expect as { paths?: Record<string, string>; error?: string }
    if (want.error) {
      let caught: unknown = null
      try {
        renameNode(g, c.id, c.new_name)
      } catch (e) {
        caught = e
      }
      expect(caught).toBeInstanceOf(PathError)
      expect((caught as PathError).code).toBe(want.error)
    } else {
      const renamed = renameNode(g, c.id, c.new_name)
      const got: Record<string, string> = {}
      for (const id of Object.keys(renamed.nodes)) got[id] = nodePath(renamed, id)
      expect(got).toEqual(want.paths)
    }
    expect(JSON.stringify(g)).toBe(before) // the input is never changed
  })
})

describe('paths vectors: rewritePathRefs (output_group ticker)', () => {
  const tickersOf = (g: PathGraph) => {
    const out: Record<string, unknown> = {}
    for (const [id, n] of Object.entries(g.nodes)) {
      const node = n as PathGraph['nodes'][string] & { type?: string; params?: Record<string, unknown> }
      if (node.type === 'output_group') out[id] = node.params?.ticker
    }
    return out
  }
  it.each(vectors.rewrite_path_refs)('$why', (c) => {
    const g = graphs[c.graph]
    const before = JSON.stringify(g)
    const renamed = renameNode(g, c.id, c.new_name)
    expect(tickersOf(renamed)).toEqual(c.expect.tickers)
    const other = renamed.nodes.n_other as unknown as { params?: Record<string, unknown> }
    expect(other.params?.ticker).toBe('/aapl') // a non-group ticker param is not a path ref
    expect(JSON.stringify(g)).toBe(before)
  })
})
