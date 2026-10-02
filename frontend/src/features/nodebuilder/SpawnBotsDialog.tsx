/**
 * Spawn bots dialog (surface S34, plan W5 item 5.F, D7).
 *
 * Turns the SAVED graph into one stopped paper-trading bot per Output
 * Group: capital, broker, data source and interval per leg, sent in ONE
 * `spawnBots(graphId, rev, legs)` call that the server runs all or nothing.
 *
 * Rules this file keeps (S34 "Must not"):
 * - Bots are always created stopped. There is no "start now" option.
 * - One request for every checked leg, never one per leg.
 * - No graph JSON from the client: the server loads revision `rev`. So
 *   unsaved edits block the table until the graph is saved.
 * - The same-symbol hint is a courtesy; the server is the guard.
 * - The leg settings are not written into the graph.
 *
 * Parts:
 * - `SpawnBotsDialog`: the dialog itself, driven by props (tests use it).
 * - `SpawnBotsButton`: the toolbar button (`toolbarRight` slot, order 50).
 * - `SpawnBotsDialogHost`: the `dialogs` slot entry that wires the dialog
 *   to the store and the builder.
 * - `SpawnToastHost`: the `overlays` slot entry for the "Created" toast.
 * The open state, toast, remembered broker/source and error copy are in
 * spawnUi.ts. The registrations live in plugins/spawnBots.ts.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Dialog } from './ui/Dialog'
import { Button } from './ui/Button'
import { useBuilder } from './slots'
import { hasEdits, useNodeBuilderStore } from './store'
import { useDiagnostics } from './useDiagnostics'
import { useSidebarWindow } from './sidebarWindow'
import { readGraphRunSettings } from './graphRun'
import {
  directionLabel,
  formatCapital,
  listGraphGroups,
  parseCapital,
  splitCapital,
  type GraphGroupInfo,
} from './graphGroups'
import { requestOpenTrading } from './graphLinks'
import {
  graphActionError,
  spawnBots,
  type GraphActionError,
  type SpawnBroker,
  type SpawnDataSource,
  type SpawnGates,
  type SpawnLeg,
  type SpawnResult,
} from '../../api/graphSpawn'
import {
  BROKER_KEY,
  BROKERS,
  closeSpawnDialog,
  defaultBotName,
  defaultSourceFor,
  dirWord,
  initialBroker,
  initialSource,
  namedGroups,
  openSpawnDialog,
  plural,
  showSpawnToast,
  SOURCE_KEY,
  SOURCES,
  spawnErrorView,
  useSpawnUi,
  writePref,
} from './spawnUi'
import { getGraph } from '../../api/graphs'
import { BOT_DEPLOYABLE_INTERVALS } from '../../shared/constants'

// ---------------------------------------------------------------------------
// The dialog
// ---------------------------------------------------------------------------

interface LegDraft {
  include: boolean
  /** null = untouched: the default name, which follows the graph name. */
  name: string | null
  /** null = untouched: the weighted share of the sidebar capital. */
  capital: string | null
  broker: SpawnBroker
  source: SpawnDataSource
  /** True once the user picked a source; a broker change then leaves it alone. */
  sourceTouched: boolean
  /** '' = the group's own interval (sends null). */
  interval: string
}

export interface SpawnBotsDialogProps {
  /** Saved graph id, or null for a graph never saved. */
  graphId: string | null
  graphName: string
  /** Saved rev, or null for a graph never saved. */
  rev: number | null
  groups: GraphGroupInfo[]
  /** Edits not saved yet. */
  dirty: boolean
  /** Validation errors in the graph now (Run's count). */
  errorCount: number
  /** The sidebar capital the default split starts from. */
  initialCapital: number
  /** The sidebar direction, sent on the implicit `main` group's leg (D7). */
  implicitDirection?: 'long' | 'short' | null
  /**
   * The sidebar gates the graph backtest ran with (trading hours,
   * skip-after-stop, dynamic sizing), sent once for every leg (LM-5).
   */
  gates?: SpawnGates
  onClose: () => void
  /** "Save now" in the unsaved bar. */
  onSaveNow: () => void
  /** "Reload graph" after a 409. */
  onReload: () => void | Promise<void>
  /** "Show diagnostics": close and open the diagnostics list. */
  onShowDiagnostics: () => void
  /** After a 201: close and show the toast. */
  onCreated: (result: SpawnResult) => void
}

export function SpawnBotsDialog(props: SpawnBotsDialogProps) {
  const { graphId, graphName, rev, groups, dirty, errorCount, initialCapital } = props
  const uid = useId()
  const hintId = `${uid}-hint`
  const [drafts, setDrafts] = useState<Record<string, LegDraft>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<GraphActionError | null>(null)
  const [errorRows, setErrorRows] = useState<Set<string>>(() => new Set())
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  // The broker and source a new row starts with (read once per open).
  const [prefs] = useState(() => {
    const broker = initialBroker()
    return { broker, source: initialSource(broker) }
  })

  const defaultCapitals = useMemo(() => splitCapital(initialCapital, groups), [initialCapital, groups])

  const draftOf = (g: GraphGroupInfo): LegDraft =>
    drafts[g.name] ?? {
      // A weight-0 group gets no capital and does not trade (S32a): its leg
      // starts unchecked.
      include: g.weight > 0,
      name: null,
      capital: null,
      broker: prefs.broker,
      source: prefs.source,
      sourceTouched: false,
      interval: '',
    }

  const update = (g: GraphGroupInfo, patch: Partial<LegDraft>) => {
    setDrafts(d => ({ ...d, [g.name]: { ...draftOf(g), ...(d[g.name] ?? {}), ...patch } }))
  }

  // The implicit group's direction is the sidebar's (D7): shown and sent.
  const directionOf = (g: GraphGroupInfo): GraphGroupInfo['direction'] =>
    g.implicit ? (props.implicitDirection ?? 'long') : g.direction
  const rows = groups.map((g, i) => {
    const d = draftOf(g)
    const capitalText = d.capital ?? formatCapital(defaultCapitals[i] ?? 0)
    const capital = parseCapital(capitalText)
    const nameText = d.name ?? defaultBotName(graphName, g.name)
    return { g, d, capitalText, capital, capitalOk: Number.isFinite(capital) && capital > 0, nameText }
  })
  const checked = rows.filter(r => r.d.include)
  const total = checked.reduce((s, r) => s + (r.capitalOk ? r.capital : 0), 0)
  const badCapital = checked.some(r => !r.capitalOk)

  // Courtesy hint: two checked legs on the same symbol and direction (S34).
  const dupRows = new Set<string>()
  const dupHints: string[] = []
  {
    const byKey = new Map<string, string[]>()
    for (const r of checked) {
      const dir = directionOf(r.g)
      if (!r.g.symbol || !dir) continue
      const key = `${r.g.symbol}|${dir}`
      byKey.set(key, [...(byKey.get(key) ?? []), r.g.name])
    }
    for (const [key, names] of byKey) {
      if (names.length < 2) continue
      names.forEach(n => dupRows.add(n))
      const [symbol] = key.split('|')
      const first = rows.find(r => r.g.name === names[0])
      const dir = dirWord(first ? directionOf(first.g) : null)
      const list = names.length === 2 ? `${names[0]} and ${names[1]}` : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
      dupHints.push(`${list} trade ${symbol} ${dir}. The server refuses this.`)
    }
  }

  const neverSaved = graphId == null || rev == null
  const blockedBySave = neverSaved || dirty
  const tableDisabled = blockedBySave || submitting
  const noGroups = groups.length === 0

  let primaryReason: string | null = null
  if (neverSaved) primaryReason = 'Save the graph first.'
  else if (dirty) primaryReason = 'Save the graph first. Bots pin a saved revision.'
  else if (errorCount > 0) primaryReason = `Fix ${plural(errorCount, 'error')} before spawning.`
  else if (noGroups) primaryReason = 'This graph has no Output Group, so there is nothing to spawn.'
  else if (checked.length === 0) primaryReason = 'Pick at least one group'
  else if (badCapital) primaryReason = 'Capital must be a number above 0.'
  const primaryDisabled = primaryReason != null || submitting

  const submit = async () => {
    if (primaryDisabled || graphId == null || rev == null) return
    const legs: SpawnLeg[] = checked.map(r => {
      const typed = r.nameText.trim()
      const dflt = defaultBotName(graphName, r.g.name)
      return {
        group: r.g.name,
        allocated_capital: r.capital,
        broker: r.d.broker,
        data_source: r.d.source,
        interval_override: r.d.interval === '' ? null : r.d.interval,
        strategy_name: typed === '' || typed === dflt ? null : typed,
        ...(r.g.implicit && props.implicitDirection ? { direction: props.implicitDirection } : {}),
      }
    })
    setSubmitting(true)
    setError(null)
    setErrorRows(new Set())
    try {
      const result = await spawnBots(graphId, rev, legs, props.gates ?? {})
      // Remember the broker and source for next time (never capital or names).
      writePref(BROKER_KEY, legs[0].broker)
      writePref(SOURCE_KEY, legs[0].data_source)
      props.onCreated(result)
    } catch (e) {
      const err = graphActionError(e)
      if (!mounted.current) return
      setError(err)
      if (err.code === 'group_unknown' || err.code === 'same_symbol_same_direction' || err.code === 'reference_unavailable') {
        setErrorRows(namedGroups(err, groups))
      }
    } finally {
      if (mounted.current) setSubmitting(false)
    }
  }

  const cancel = () => {
    if (submitting) return
    props.onClose()
  }

  const errorView = error ? spawnErrorView(error) : null

  const title = (
    <>
      Spawn bots from <span style={styles.mono}>{graphName || 'untitled'}</span>
    </>
  )
  const n = checked.length
  const footerLeft = (
    <span style={styles.footerNote} data-testid="nb-spawn-total">
      {plural(n, 'bot')} · {formatCapital(total)} total capital
    </span>
  )

  let saveBar: ReactNode = null
  if (blockedBySave) {
    saveBar = (
      <div className="nb-banner nb-banner--warn" style={styles.bar} data-testid="nb-spawn-save-bar">
        <span className="nb-banner__text">{neverSaved ? 'Save the graph first.' : 'Save the graph first. Bots pin a saved revision.'}</span>
        <Button onClick={props.onSaveNow} data-testid="nb-spawn-save-now">Save now</Button>
      </div>
    )
  } else if (errorCount > 0) {
    saveBar = (
      <div className="nb-banner nb-banner--warn" style={styles.bar} data-testid="nb-spawn-errors-bar">
        <span className="nb-banner__text">Fix {plural(errorCount, 'error')} before spawning.</span>
        <button type="button" className="nb-banner__link" onClick={props.onShowDiagnostics}>Show diagnostics</button>
      </div>
    )
  }

  return (
    <Dialog
      title={title}
      onCancel={cancel}
      width={720}
      headerExtra={rev != null ? <span style={styles.rev} data-testid="nb-spawn-rev">rev {rev}</span> : null}
      footerLeft={footerLeft}
      primaryLabel={
        submitting ? (
          <>
            <span className="nb-spinner" aria-hidden="true" />
            Creating…
          </>
        ) : (
          `Create ${plural(n, 'bot')}`
        )
      }
      onPrimary={() => void submit()}
      primaryDisabled={primaryDisabled}
      primaryDisabledReason={submitting ? 'Creating…' : primaryReason ?? undefined}
      // Only Cmd/Ctrl+Enter (anywhere in the dialog) and the click create
      // bots; a stray Enter in a field never does (S34, UX-08).
      submitKey="mod-enter"
      data-testid="nb-spawn-dialog"
    >
      <div style={styles.body}>
        <div style={styles.notice}>Every bot is created stopped. Start them from the Trading tab when you are ready.</div>
        {saveBar}
        {noGroups ? (
          <div style={styles.empty} data-testid="nb-spawn-empty">
            This graph has no Output Group, so there is nothing to spawn.
          </div>
        ) : (
          <table style={{ ...styles.table, opacity: blockedBySave ? 0.45 : 1 }} aria-describedby={hintId} data-testid="nb-spawn-table">
            <thead>
              <tr className="nb-caps" style={styles.headRow}>
                <th scope="col" style={styles.th}>Group</th>
                <th scope="col" style={styles.th}>Bot name</th>
                <th scope="col" style={styles.th}>Capital</th>
                <th scope="col" style={styles.th}>Broker</th>
                <th scope="col" style={styles.th}>Data source</th>
                <th scope="col" style={styles.th}>Interval</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const { g, d } = r
                const marked = errorRows.has(g.name) ? 'var(--nb-error)' : dupRows.has(g.name) ? 'var(--nb-warn)' : null
                return (
                  <tr key={g.name} style={styles.row} data-testid={`nb-spawn-row-${g.name}`} data-marked={marked ? 'true' : undefined}>
                    <td style={{ ...styles.td, ...styles.groupCell, boxShadow: marked ? `inset 2px 0 0 ${marked}` : undefined }}>
                      <div style={styles.groupLine}>
                        <input
                          type="checkbox"
                          aria-label={`Include ${g.name}`}
                          checked={d.include}
                          disabled={tableDisabled}
                          onChange={e => update(g, { include: e.target.checked })}
                          style={styles.check}
                        />
                        <span style={styles.glyph} aria-hidden="true">O</span>
                        <span style={styles.groupName}>{g.name}</span>
                        <DirectionPill direction={directionOf(g)} />
                      </div>
                      {g.symbol && (
                        <div style={styles.tickerLine}>
                          <span style={styles.tickerChip}>{g.interval ? `${g.symbol} · ${g.interval}` : g.symbol}</span>
                        </div>
                      )}
                    </td>
                    <td style={styles.td}>
                      <input
                        type="text"
                        className="nb-input nb-input--mono"
                        aria-label={`Bot name for ${g.name}`}
                        value={r.nameText}
                        disabled={tableDisabled}
                        onChange={e => update(g, { name: e.target.value })}
                        style={{ ...styles.field, width: 160 }}
                      />
                    </td>
                    <td style={styles.td}>
                      <input
                        type="text"
                        inputMode="decimal"
                        className="nb-input nb-input--mono"
                        aria-label={`Capital for ${g.name}`}
                        aria-invalid={d.include && !r.capitalOk ? true : undefined}
                        value={r.capitalText}
                        disabled={tableDisabled}
                        onChange={e => update(g, { capital: e.target.value })}
                        style={{ ...styles.field, width: 96 }}
                      />
                    </td>
                    <td style={styles.td}>
                      <select
                        className="nb-input"
                        aria-label={`Broker for ${g.name}`}
                        value={d.broker}
                        disabled={tableDisabled}
                        onChange={e => {
                          const broker = e.target.value as SpawnBroker
                          update(g, d.sourceTouched ? { broker } : { broker, source: defaultSourceFor(broker) })
                        }}
                        style={{ ...styles.field, width: 96 }}
                      >
                        {BROKERS.map(b => <option key={b.value} value={b.value}>{b.label}</option>)}
                      </select>
                    </td>
                    <td style={styles.td}>
                      <select
                        className="nb-input"
                        aria-label={`Data source for ${g.name}`}
                        value={d.source}
                        disabled={tableDisabled}
                        onChange={e => update(g, { source: e.target.value as SpawnDataSource, sourceTouched: true })}
                        style={{ ...styles.field, width: 112 }}
                      >
                        {SOURCES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                      </select>
                    </td>
                    <td style={styles.td}>
                      <select
                        className="nb-input"
                        aria-label={`Interval for ${g.name}`}
                        value={d.interval}
                        disabled={tableDisabled}
                        onChange={e => update(g, { interval: e.target.value })}
                        style={{ ...styles.field, width: 72 }}
                      >
                        <option value="">{`graph (${g.interval || '?'})`}</option>
                        {BOT_DEPLOYABLE_INTERVALS.map(v => <option key={v} value={v}>{v}</option>)}
                      </select>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        <div id={hintId} style={styles.hints}>
          {!blockedBySave && badCapital && (
            <div style={styles.hintError} data-testid="nb-spawn-capital-hint">Capital must be a number above 0.</div>
          )}
          {dupHints.map(h => (
            <div key={h} style={styles.hintWarn} data-testid="nb-spawn-dup-hint">{h}</div>
          ))}
        </div>
        {errorView && (
          <div className="nb-banner nb-banner--error" style={styles.bar} role="alert" data-testid="nb-spawn-error">
            <span className="nb-banner__text">{errorView.text}</span>
            {errorView.action === 'reload' && (
              <Button
                onClick={async () => {
                  await props.onReload()
                  if (mounted.current) setError(null)
                }}
                data-testid="nb-spawn-reload"
              >
                Reload graph
              </Button>
            )}
            {errorView.action === 'diagnostics' && (
              <button type="button" className="nb-banner__link" onClick={props.onShowDiagnostics}>Show diagnostics</button>
            )}
            {errorView.action === 'retry' && (
              <button type="button" className="nb-banner__link" onClick={() => void submit()} data-testid="nb-spawn-retry">Retry</button>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}

/** LONG / SHORT / SWITCH pill (shared rules for W5 surfaces). */
export function DirectionPill({ direction }: { direction: GraphGroupInfo['direction'] }) {
  if (!direction) return null
  const look =
    direction === 'short'
      ? { background: 'rgba(248,113,113,0.16)', color: 'var(--nb-error)' }
      : direction === 'regime_switch'
        ? { background: 'var(--nb-tint-network)', color: 'var(--nb-cat-network)' }
        : { background: 'rgba(52,211,153,0.16)', color: 'var(--nb-ok)' }
  return <span style={{ ...styles.pill, ...look }}>{directionLabel(direction)}</span>
}

// ---------------------------------------------------------------------------
// Slot components
// ---------------------------------------------------------------------------

/** `Spawn bots…` in the toolbar. Shown for an editable graph only. */
export function SpawnBotsButton() {
  const editable = useNodeBuilderStore(s => s.graph != null && !s.graph.readOnly)
  if (!editable) return null
  return (
    <Button
      onClick={openSpawnDialog}
      title="Create one stopped bot per Output Group"
      aria-haspopup="dialog"
      data-testid="nb-btn-spawn"
    >
      Spawn bots…
    </Button>
  )
}

/** The `dialogs` slot entry: the open dialog, wired to the editor. */
export function SpawnBotsDialogHost() {
  const open = useSpawnUi(s => s.open)
  const pending = useSpawnUi(s => s.pendingGraphId)
  const graphId = useNodeBuilderStore(s => s.graphMeta?.id ?? null)
  const editable = useNodeBuilderStore(s => s.graph != null && !s.graph.readOnly)

  // The Trading view asked to spawn from a graph: open once it is loaded.
  useEffect(() => {
    if (pending != null && editable && graphId === pending) {
      useSpawnUi.setState({ pendingGraphId: null, open: true })
    }
  }, [pending, graphId, editable])

  if (!open || !editable) return null
  return <ConnectedSpawnDialog />
}

function ConnectedSpawnDialog() {
  const builder = useBuilder()
  const graph = useNodeBuilderStore(s => s.graph)
  const meta = useNodeBuilderStore(s => s.graphMeta)
  const dirty = useNodeBuilderStore(s => hasEdits(s))
  const diagnostics = useDiagnostics()
  const sidebar = useSidebarWindow()
  const [capital] = useState(() => readGraphRunSettings().initial_capital)
  const [implicitDirection] = useState(() => readGraphRunSettings().extras.direction ?? null)
  // The gates the graph backtest used (graphRun applyExtras), read once per open.
  const [gates] = useState<SpawnGates>(() => {
    const { extras } = readGraphRunSettings()
    const out: SpawnGates = {}
    if (extras.trading_hours != null) out.trading_hours = extras.trading_hours
    if (extras.skip_after_stop != null) out.skip_after_stop = extras.skip_after_stop
    if (extras.dynamic_sizing != null) out.dynamic_sizing = extras.dynamic_sizing
    return out
  })

  const groups = useMemo(() => {
    const list = listGraphGroups(graph)
    if (!sidebar) return list
    // A group whose ticker could not be read shows the sidebar's (the window graph runs use).
    return list.map(g => (g.symbol ? g : { ...g, symbol: sidebar.ticker.toUpperCase(), interval: g.interval || sidebar.interval }))
  }, [graph, sidebar])

  const showDiagnostics = () => {
    closeSpawnDialog()
    // After the dialog has handed focus back, open the graph-wide list under the chip.
    setTimeout(() => {
      const anchor =
        document.querySelector<HTMLElement>('[data-testid="nb-diag-chip"]') ??
        document.querySelector<HTMLElement>('[data-testid="nb-btn-spawn"]')
      if (anchor) builder?.openDiagnostics(anchor)
    }, 0)
  }

  const reload = async () => {
    const id = useNodeBuilderStore.getState().graphMeta?.id
    if (!id) return
    const session = builder?.session as { reload?: () => void | Promise<void> } | undefined
    if (session?.reload) {
      await session.reload()
      return
    }
    // No session reload: load the saved graph ourselves. Only a clean editor
    // is replaced (the dialog blocks spawning while edits are unsaved).
    try {
      const seq = useNodeBuilderStore.getState().commitSeq
      const env = await getGraph(id)
      const s = useNodeBuilderStore.getState()
      if (s.commitSeq !== seq || hasEdits(s) || s.graphMeta?.id !== id) return
      s.openGraph(env.graph.readOnly ? { ...env.graph, readOnly: false } : env.graph, { id: env.id, rev: env.rev, name: env.name })
    } catch (e) {
      console.warn('[nodebuilder] could not reload the graph', e)
    }
  }

  return (
    <SpawnBotsDialog
      graphId={meta?.id ?? null}
      graphName={meta?.name ?? ''}
      rev={meta?.id ? meta.rev : null}
      groups={groups}
      dirty={dirty}
      errorCount={diagnostics.errorCount}
      initialCapital={capital}
      implicitDirection={implicitDirection}
      gates={gates}
      onClose={closeSpawnDialog}
      onSaveNow={() => builder?.session.save()}
      onReload={reload}
      onShowDiagnostics={showDiagnostics}
      onCreated={result => {
        closeSpawnDialog()
        const count = result.bots?.length ?? 0
        showSpawnToast(`Created ${plural(count, 'stopped bot')}.`, result.bots?.[0]?.bot_id ?? null)
      }}
    />
  )
}

/** The `overlays` slot entry: the "Created N stopped bots" toast (4 s, one link). */
export function SpawnToastHost() {
  const toast = useSpawnUi(s => s.toast)
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => {
      if (useSpawnUi.getState().toast?.seq === toast.seq) useSpawnUi.setState({ toast: null })
    }, 4000)
    return () => clearTimeout(t)
  }, [toast])
  if (!toast) return null
  return (
    <div role="status" style={styles.toast} data-testid="nb-spawn-toast">
      <span>{toast.text}</span>
      <button
        type="button"
        className="nb-banner__link"
        onClick={() => {
          useSpawnUi.setState({ toast: null })
          requestOpenTrading(toast.botId)
        }}
      >
        Open Trading
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Styles (tokens only)
// ---------------------------------------------------------------------------

const styles: Record<string, CSSProperties> = {
  mono: { fontFamily: 'var(--nb-font-mono)' },
  rev: { marginLeft: 'auto', marginRight: 8, fontFamily: 'var(--nb-font-mono)', fontSize: 11, color: 'var(--nb-text-muted)' },
  body: { display: 'flex', flexDirection: 'column', gap: 8, fontFamily: 'var(--nb-font-sans)', fontSize: 12 },
  notice: { minHeight: 32, display: 'flex', alignItems: 'center', color: 'var(--nb-text-secondary)', fontSize: 12 },
  bar: { minHeight: 32, display: 'flex', alignItems: 'center', gap: 8, padding: '0 8px', borderRadius: 4, borderBottom: 'none' },
  empty: { padding: '16px 0', color: 'var(--nb-text-muted)' },
  table: { width: '100%', borderCollapse: 'collapse' },
  headRow: { height: 28, color: 'var(--nb-text-muted)' },
  th: { textAlign: 'left', fontWeight: 600, padding: '0 6px 0 0' },
  row: { height: 40, borderTop: '1px solid var(--nb-border-subtle)' },
  td: { padding: '4px 6px 4px 0', verticalAlign: 'middle' },
  groupCell: { width: 180, paddingLeft: 4 },
  groupLine: { display: 'flex', alignItems: 'center', gap: 6 },
  check: { width: 12, height: 12, margin: 0 },
  glyph: {
    width: 12, height: 12, borderRadius: 3, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 9, fontWeight: 600, color: 'var(--nb-cat-output)', background: 'var(--nb-tint-output)',
  },
  groupName: { fontWeight: 600, color: 'var(--nb-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  tickerLine: { marginTop: 2, paddingLeft: 18 },
  tickerChip: {
    display: 'inline-block', height: 16, lineHeight: '16px', padding: '0 5px', borderRadius: 3,
    fontFamily: 'var(--nb-font-mono)', fontWeight: 500, fontSize: 10,
    background: 'var(--nb-tint-ticker)', color: 'var(--nb-cat-ticker)',
  },
  pill: {
    display: 'inline-block', height: 16, lineHeight: '16px', padding: '0 5px', borderRadius: 3,
    fontWeight: 600, fontSize: 10, letterSpacing: '0.06em',
  },
  field: { height: 28, boxSizing: 'border-box', fontSize: 12, borderRadius: 4, padding: '0 6px' },
  hints: { display: 'flex', flexDirection: 'column', gap: 2 },
  hintError: { color: 'var(--nb-error)', fontSize: 11 },
  hintWarn: { color: 'var(--nb-warn)', fontSize: 11 },
  footerNote: { fontFamily: 'var(--nb-font-mono)', fontSize: 11, color: 'var(--nb-text-muted)' },
  toast: {
    position: 'fixed', left: '50%', bottom: 48, transform: 'translateX(-50%)', zIndex: 10002,
    height: 36, display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px', borderRadius: 6,
    background: 'var(--nb-bg-elevated)', border: '1px solid var(--nb-border-strong)', boxShadow: 'var(--nb-shadow-popover)',
    fontFamily: 'var(--nb-font-sans)', fontSize: 12, color: 'var(--nb-text)',
  },
}

export default SpawnBotsDialog
