/**
 * Graph lookups for the code UI (F435 W7) that need the store and the last
 * validate: kept apart from codeOps.ts, which stays pure.
 */

import type { Graph } from '../../../api/nodebuilder'
import { writesOf } from '../streamLabels'
import { getStreams } from '../useDiagnostics'
import { useCodeStore } from './codeStore'

/**
 * Every attribute name the graph already writes (`@rsi`, `@out`): catalog
 * write params, the last validate's streams, and the writes of every code
 * snippet parsed so far. A new Wrangle's default write avoids them (S46).
 */
export function attrNamesInGraph(graph: Pick<Graph, 'nodes'>): Set<string> {
  const out = new Set<string>()
  for (const node of Object.values(graph.nodes)) {
    for (const w of writesOf(node)) out.add(w.name)
  }
  for (const s of Object.values(getStreams())) {
    for (const a of s.points) out.add(a.name)
    for (const a of s.detail) out.add(a.name)
  }
  for (const entry of Object.values(useCodeStore.getState().parses)) {
    for (const w of entry.res.writes) out.add(w.name)
  }
  return out
}
