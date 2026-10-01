/**
 * The Inspector with one wire selected (S14): header `src → dst` and
 * `out → port`; a Wire section with the two ends as links, the ports and the
 * attributes read through this wire; a Stream section with the source's
 * whole output stream; and, at the bottom, `Delete wire` (one undo step,
 * the same path as the Delete key).
 */

import type { Graph, GraphWire } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { portsOf, readsThroughWire, removeWiresWithTerms } from '../streamLabels'
import { useNodeStream, useStreams, useStreamsFresh } from '../useDiagnostics'
import { Button } from '../ui/Button'
import { InspectorSectionShell } from './Section'
import { catVars, categoryOf, focusCanvas, useInspectorGraph } from './util'

/**
 * Delete a wire the way the Delete key does (UX-09): one undo step labelled
 * `delete wire`, and a wire into AND / OR / XOR takes back the term it
 * added. Then nothing is selected and the canvas has the keys again.
 */
function deleteWireFromInspector(wireId: string): void {
  const s = useNodeBuilderStore.getState()
  s.commit('delete wire', g => removeWiresWithTerms(g, [wireId]))
  s.select(null)
  focusCanvas()
}

function portLabel(graph: Graph, wire: GraphWire): string {
  const to = graph.nodes[wire.to]
  if (!to) return wire.to_port
  const port = portsOf(to.type, [wire.to_port]).find(p => p.id === wire.to_port)
  return port?.label ?? wire.to_port
}

export function WireView({ wireId, editable }: { wireId: string; editable: boolean }) {
  const { graph } = useInspectorGraph()
  const streams = useStreams()
  const fresh = useStreamsFresh()
  const wire = graph?.wires.find(w => w.id === wireId)
  const sourceStream = useNodeStream(wire?.from)
  const setSelection = useNodeBuilderStore(s => s.setSelection)
  if (!graph || !wire) return null
  const from = graph.nodes[wire.from]
  const to = graph.nodes[wire.to]
  const fromName = from?.name ?? wire.from
  const toName = to?.name ?? wire.to
  const port = portLabel(graph, wire)
  const reads = readsThroughWire(wire, graph, streams, undefined, fresh)
  const attrs = sourceStream ? [...sourceStream.points, ...sourceStream.detail] : []

  return (
    <>
      <div className="nb-insp-head">
        <span className="nb-insp-head__glyph nb-insp-head__glyph--plain" aria-hidden="true">→</span>
        <div className="nb-insp-head__text">
          <div className="nb-insp-head__line1">
            <span className="nb-insp-head__title" data-testid="nb-inspector-wire-title">{fromName} → {toName}</span>
          </div>
          <div className="nb-insp-head__path">out → {port}</div>
        </div>
      </div>
      <InspectorSectionShell id="wire" title="Wire">
        <div className="nb-insp-kv">
          <span className="nb-insp-kv__k">from</span>
          <button type="button" className="nb-btn nb-btn--text nb-insp-link" onClick={() => setSelection({ nodeIds: [wire.from] })}>
            {fromName}
          </button>
          <span className="nb-insp-kv__v">out</span>
        </div>
        <div className="nb-insp-kv">
          <span className="nb-insp-kv__k">to</span>
          <button type="button" className="nb-btn nb-btn--text nb-insp-link" onClick={() => setSelection({ nodeIds: [wire.to] })}>
            {toName}
          </button>
          <span className="nb-insp-kv__v" data-testid="nb-inspector-wire-port">{port}</span>
        </div>
        <div className="nb-insp-lbl">reads through this wire</div>
        <div className="nb-insp-chips" data-testid="nb-inspector-wire-reads">
          {reads.length === 0
            ? <span className="nb-insp-dim">nothing yet</span>
            : reads.map(r => <span key={r} className="nb-chip">{r}</span>)}
        </div>
      </InspectorSectionShell>
      <InspectorSectionShell id="stream" title="Stream" count={attrs.length ? `${attrs.length} attrs` : undefined}>
        {attrs.length === 0 ? (
          <span className="nb-insp-dim">The stream shows after the graph is checked.</span>
        ) : (
          <div className="nb-insp-chips">
            {attrs.map(a => {
              const writer = a.written_by ? graph.nodes[a.written_by] : undefined
              return (
                <span key={`${a.name}:${a.written_by ?? ''}`} className="nb-chip nb-chip--write nb-chip--readonly"
                  style={catVars(writer ? categoryOf(writer.type) : null)}
                  title={writer ? `written by ${writer.name}` : undefined}>
                  {a.name}
                </span>
              )
            })}
          </div>
        )}
      </InspectorSectionShell>
      {editable && (
        // At the bottom of the view, under every section (S14).
        <div className="nb-insp-actions">
          <Button kind="text" className="nb-insp-danger" data-testid="nb-inspector-delete-wire" onClick={() => deleteWireFromInspector(wire.id)}>
            Delete wire
          </Button>
        </div>
      )}
    </>
  )
}
