/**
 * The Inspector with nothing selected (spec S15). It teaches the editor:
 * the graph's name and description, what the category colors mean (click a
 * row to select every node of that category, Shift adds), what the two flag
 * dots do, and the dozen keys that matter most.
 *
 * The Keys list is a fixed, curated twelve, not built from listCommands()
 * (the `?` overlay lists everything).
 */

import { useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import type { Graph } from '../../api/nodebuilder'
import { NODE_CATALOG } from './catalog'
import { runCommand } from './commands'
import { InspectorSectionShell } from './inspector/Section'
import { commandForKeys, focusCanvas } from './inspector/util'
import { isMacPlatform } from './graphText'
import { formatChord } from './shortcutList'
import { useBuilder } from './slots'
import { useNodeBuilderStore } from './store'
import { useDiagnostics } from './useDiagnostics'

/** Legend rows in palette order; only categories in the catalog are shown. */
const LEGEND_CATEGORIES: readonly { key: string; name: string }[] = [
  { key: 'ticker', name: 'Tickers' },
  { key: 'data', name: 'Data' },
  { key: 'indicator', name: 'Indicators' },
  { key: 'comparison', name: 'Comparisons' },
  { key: 'logic', name: 'Logic' },
  { key: 'signal', name: 'Math & Signal' },
  { key: 'rules', name: 'Rules' },
  { key: 'settings', name: 'Settings' },
  { key: 'code', name: 'Code / Wrangle' },
  { key: 'output', name: 'Outputs' },
  { key: 'network', name: 'Networks' },
]

const CATALOG_CATS = new Set(NODE_CATALOG.map(e => e.cat))
const CAT_OF_TYPE = new Map(NODE_CATALOG.map(e => [e.name, e.cat]))

/**
 * The twelve keys of S15, in order. `chord` rows use the same platform-aware
 * formatter as the `?` overlay and the menus (UX-15: `⌘Z` on a Mac,
 * `Ctrl+Z` elsewhere); `text` rows are gestures, not chords.
 */
const LEGEND_KEYS: readonly { label: string; chord?: string; text?: (mac: boolean) => string }[] = [
  { label: 'Add node', chord: 'tab' },
  { label: 'Pan', text: () => 'Space+drag' },
  { label: 'Zoom', text: () => 'wheel' },
  { label: 'Marquee', text: () => 'drag empty' },
  { label: 'Multi-select', text: mac => (mac ? '⇧click' : 'Shift+click') },
  { label: 'Frame selection', chord: 'f' },
  { label: 'Frame all', chord: 'h' },
  { label: 'Bypass', chord: 'b' },
  { label: 'Display', chord: 'd' },
  { label: 'Undo', chord: 'mod+z' },
  { label: 'Delete', chord: 'backspace' },
  { label: 'Data sheet', chord: 's' },
]

function GraphName({ editable }: { editable: boolean }) {
  const builder = useBuilder()
  const name = useNodeBuilderStore(s => s.graphMeta?.name) ?? 'untitled'
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(name)
  const [error, setError] = useState<string | null>(null)
  const doneRef = useRef(false)
  const canEdit = editable && builder != null

  const commit = async () => {
    const next = value.trim()
    doneRef.current = true
    if (!next || next === name || !builder) { setEditing(false); return }
    const problem = await builder.session.renameInline(next)
    if (problem) { setError(problem); doneRef.current = false; return }
    setEditing(false)
  }

  if (editing) {
    return (
      <span className="nb-insp-graph__edit">
        <input
          className="nb-insp-field"
          value={value}
          autoFocus
          aria-label="Graph name"
          data-testid="nb-inspector-graph-name-input"
          onChange={e => { setValue(e.target.value); setError(null) }}
          onKeyDown={e => {
            if (e.key === 'Enter') { e.preventDefault(); void commit() }
            else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); doneRef.current = true; setEditing(false); focusCanvas() }
          }}
          onBlur={() => { if (!doneRef.current) void commit() }}
        />
        {error && <span className="nb-insp-head__help" role="alert">{error}</span>}
      </span>
    )
  }
  return (
    <button
      type="button"
      className="nb-insp-graph__name"
      data-testid="nb-inspector-graph-name"
      disabled={!canEdit}
      title={canEdit ? 'Rename' : undefined}
      onClick={() => { doneRef.current = false; setValue(name); setError(null); setEditing(true) }}
    >
      {name}
    </button>
  )
}

function GraphDescription({ graph, editable }: { graph: Graph; editable: boolean }) {
  const stored = typeof graph.meta?.description === 'string' ? graph.meta.description : ''
  // While focused the typed text wins; otherwise the field shows the graph.
  const [draft, setDraft] = useState<string | null>(null)
  // Set by Esc so the blur that follows does not save the typed text.
  const cancelRef = useRef(false)
  if (!editable) return <span className="nb-insp-graph__desc">{stored || '—'}</span>
  return (
    <input
      className="nb-insp-field nb-insp-field--sans"
      value={draft ?? stored}
      placeholder="Add a description…"
      aria-label="Graph description"
      data-testid="nb-inspector-graph-description"
      onFocus={() => { cancelRef.current = false; setDraft(stored) }}
      onChange={e => setDraft(e.target.value)}
      onKeyDown={e => {
        if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() }
        else if (e.key === 'Escape') { cancelRef.current = true; setDraft(null); (e.target as HTMLInputElement).blur(); focusCanvas() }
      }}
      onBlur={() => {
        if (cancelRef.current || draft === null) { setDraft(null); return }
        const text = draft.trim()
        setDraft(null)
        if (text === stored) return
        // Stored in the graph's free meta map, so it is saved with the graph
        // and undoable like any other edit.
        useNodeBuilderStore.getState().commit('edit description', g => {
          const meta = { ...g.meta }
          if (text) meta.description = text
          else delete meta.description
          return { ...g, meta }
        })
      }}
    />
  )
}

function GraphSection({ graph, editable }: { graph: Graph; editable: boolean }) {
  const builder = useBuilder()
  const rev = useNodeBuilderStore(s => s.graphMeta?.rev ?? null)
  const dirty = useNodeBuilderStore(s => s.dirty)
  const diag = useDiagnostics()
  const nodeCount = Object.keys(graph.nodes).length
  const parts: string[] = []
  if (diag.errorCount) parts.push(`${diag.errorCount} ${diag.errorCount === 1 ? 'error' : 'errors'}`)
  if (diag.warningCount) parts.push(`${diag.warningCount} ${diag.warningCount === 1 ? 'warning' : 'warnings'}`)
  return (
    <InspectorSectionShell id="graph" title="Graph">
      <div className="nb-insp-kv"><span className="nb-insp-kv__k">name</span><GraphName editable={editable} /></div>
      <div className="nb-insp-kv"><span className="nb-insp-kv__k">description</span><GraphDescription graph={graph} editable={editable} /></div>
      <div className="nb-insp-kv" data-testid="nb-inspector-graph-counts">
        <span className="nb-insp-kv__k">size</span>
        <span className="nb-insp-kv__v">nodes {nodeCount} · wires {graph.wires.length}</span>
      </div>
      {rev != null && (
        <div className="nb-insp-kv">
          <span className="nb-insp-kv__k">rev</span>
          <span className="nb-insp-kv__v">rev {rev} · {dirty ? <span className="nb-insp-warn">unsaved</span> : 'saved'}</span>
        </div>
      )}
      <div className="nb-insp-kv">
        <span className="nb-insp-kv__k">diagnostics</span>
        <button
          type="button"
          className="nb-btn nb-btn--text nb-insp-link"
          data-testid="nb-inspector-graph-diagnostics"
          onClick={e => builder?.openDiagnostics(e.currentTarget)}
        >
          {parts.length === 0
            ? <span className="nb-insp-dim">no problems</span>
            : <>
                {diag.errorCount > 0 && <span className="nb-insp-err">{parts[0]}</span>}
                {diag.errorCount > 0 && diag.warningCount > 0 && ' · '}
                {diag.warningCount > 0 && <span className="nb-insp-warn">{parts[parts.length - 1]}</span>}
              </>}
        </button>
      </div>
    </InspectorSectionShell>
  )
}

function LegendSection({ graph }: { graph: Graph | null }) {
  const counts = new Map<string, number>()
  const byCat = new Map<string, string[]>()
  for (const n of Object.values(graph?.nodes ?? {})) {
    if (n.parent !== null) continue // the current network is the root until W5
    const cat = CAT_OF_TYPE.get(n.type)
    if (!cat) continue
    counts.set(cat, (counts.get(cat) ?? 0) + 1)
    let list = byCat.get(cat)
    if (!list) byCat.set(cat, (list = []))
    list.push(n.id)
  }
  const pick = (cat: string, e: ReactMouseEvent) => {
    const s = useNodeBuilderStore.getState()
    const ids = byCat.get(cat) ?? []
    const nodeIds = e.shiftKey ? [...new Set([...s.selectedNodeIds, ...ids])] : ids
    s.setSelection({ nodeIds })
  }
  return (
    <InspectorSectionShell id="legend" title="Legend">
      <div role="list" className="nb-insp-legend">
        {LEGEND_CATEGORIES.filter(c => CATALOG_CATS.has(c.key)).map(c => {
          const n = counts.get(c.key) ?? 0
          // The list item wraps the button, so the row keeps its button role (UX-12).
          return (
            <div key={c.key} role="listitem">
              <button
                type="button"
                className="nb-insp-legend__row"
                data-testid={`nb-legend-row-${c.key}`}
                aria-label={`Select all ${c.name} (${n})`}
                disabled={!graph}
                onClick={e => pick(c.key, e)}
              >
                <span className="nb-insp-swatch" data-testid={`nb-legend-swatch-${c.key}`} style={{ background: `var(--nb-cat-${c.key})` }} />
                <span className="nb-insp-legend__name">{c.name}</span>
                {n > 0 && <span className="nb-insp-legend__count">{n}</span>}
              </button>
            </div>
          )
        })}
      </div>
    </InspectorSectionShell>
  )
}

function FlagsSection() {
  return (
    <InspectorSectionShell id="flags" title="Flags">
      <div className="nb-insp-flagrow" title="shows this node's data in the Data Sheet and chart">
        <span className="nb-insp-flagrow__dot" style={{ background: 'var(--nb-flag-display)' }} />
        <span className="nb-insp-flagrow__name">Display</span>
        <span className="nb-insp-flagrow__text">shows this node's data in the Data Sheet and chart</span>
        <kbd className="nb-insp-kbd">D</kbd>
      </div>
      <div className="nb-insp-flagrow" title="skips this node; its input passes through">
        <span className="nb-insp-flagrow__dot" style={{ background: 'var(--nb-flag-bypass)' }} />
        <span className="nb-insp-flagrow__name">Bypass</span>
        <span className="nb-insp-flagrow__text">skips this node; its input passes through</span>
        <kbd className="nb-insp-kbd">B</kbd>
      </div>
    </InspectorSectionShell>
  )
}

function KeysSection({ forceOpen }: { forceOpen: boolean }) {
  const mac = isMacPlatform()
  const help = commandForKeys(['?', 'shift+?'])
  return (
    <InspectorSectionShell id="keys" title="Keys" forceOpen={forceOpen}>
      {LEGEND_KEYS.map((k, i) => (
        <div key={k.label} className="nb-insp-keyrow" data-testid={`nb-keys-row-${i}`}>
          <span>{k.label}</span>
          <kbd className="nb-insp-kbd">{k.chord ? formatChord(k.chord, mac) : k.text!(mac)}</kbd>
        </div>
      ))}
      <AllShortcutsButton commandId={help?.id ?? null} />
    </InspectorSectionShell>
  )
}

/** Opens the `?` overlay (S21) through whichever command is bound to `?`. */
function AllShortcutsButton({ commandId }: { commandId: string | null }) {
  return (
    <button
      type="button"
      className="nb-btn nb-btn--text nb-insp-allkeys"
      data-testid="nb-inspector-all-shortcuts"
      disabled={!commandId}
      title={commandId ? undefined : 'The shortcut list is not available'}
      onClick={() => { if (commandId) runCommand(commandId) }}
    >
      Show all shortcuts (?)
    </button>
  )
}

/** S15: what the Inspector shows with no node or wire selected. */
export default function InspectorLegend({ graph, editable }: { graph: Graph | null; editable: boolean }) {
  const empty = graph != null && Object.keys(graph.nodes).length === 0
  return (
    <div data-testid="nb-inspector-empty">
      <div className="nb-insp-head nb-insp-head--empty">
        <div className="nb-insp-head__text">
          <div className="nb-insp-caps">Inspector</div>
          <div className="nb-insp-head__hint">
            {empty ? 'Press Tab to add your first node.' : 'Click a node to inspect.'}
          </div>
        </div>
      </div>
      {graph && <GraphSection graph={graph} editable={editable} />}
      <LegendSection graph={graph} />
      <FlagsSection />
      <KeysSection forceOpen={empty} />
    </div>
  )
}
