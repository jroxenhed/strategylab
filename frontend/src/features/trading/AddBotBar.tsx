import { useCallback, useEffect, useRef, useState } from 'react'
import type { BotFundStatus, SavedStrategy } from '../../shared/types'
import { fmtUsd } from '../../shared/utils/format'
import { apiErrorDetail } from '../../shared/utils/errors'
import { btnStyle } from './ui'
import { BOT_DEPLOYABLE_INTERVALS } from '../../shared/constants'
import { errorDiagnostics, getGraph, graphErrorDetail, listGraphs, type GraphEnvelope, type GraphListItem } from '../../api/graphs'
import { formatFullTimestamp, useRelativeTime } from '../nodebuilder/ui/relativeTime'
import { directionLabel, listGraphGroups, type GraphGroupInfo } from '../nodebuilder/graphGroups'

const SAVED_KEY = 'strategylab-saved-strategies'
// INTERVALS is the set of deployable intraday intervals — shared source of truth in shared/constants.ts
const INTERVALS = BOT_DEPLOYABLE_INTERVALS

/** Graph list state for the picker (S06). Graphs come from the server only. */
type GraphList =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'error'; detail: string }
  | { state: 'ready'; graphs: GraphListItem[] }

/** One fetched graph for the group selector (S36), cached by id in component state. */
type GraphLoad =
  | { state: 'loading' }
  | { state: 'error'; detail: string }
  | { state: 'ready'; env: GraphEnvelope; groups: GraphGroupInfo[] }

const NO_GROUPS: GraphGroupInfo[] = []

/** `long_leg · LONG · AAPL 1d` (S36 option format). */
function groupOptionLabel(g: GraphGroupInfo): string {
  const parts = [g.name]
  const dir = directionLabel(g.direction)
  if (dir) parts.push(dir)
  const ticker = [g.symbol, g.interval].filter(Boolean).join(' ')
  if (ticker) parts.push(ticker)
  return parts.join(' · ')
}

/** The first diagnostic's message from a 400 graph error, else null. */
function firstDiagnosticMessage(e: unknown): string | null {
  const list = errorDiagnostics(e)
  if (!list || list.length === 0) return null
  const msg = (list[0] as { message?: unknown } | null)?.message
  return typeof msg === 'string' && msg ? msg : null
}

/** Newest first, as the picker shows them. */
function sortByUpdated(graphs: GraphListItem[]): GraphListItem[] {
  return [...graphs].sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
}

/** `rev 4 · updated 5 min ago` under the picker for the chosen graph. */
function SelectedGraphLine({ graph }: { graph: GraphListItem }) {
  const rel = useRelativeTime(graph.updated_at)
  return (
    <span title={formatFullTimestamp(graph.updated_at)}>
      rev {graph.rev} · updated {rel}
    </span>
  )
}

export const sectionStyle: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #1e2530',
  borderRadius: 6,
  padding: '10px 12px',
}

export const inputStyle: React.CSSProperties = {
  background: '#0d1117',
  border: '1px solid #2a3040',
  borderRadius: 4,
  color: '#e6edf3',
  padding: '4px 8px',
  fontSize: 12,
}

export default function AddBotBar({
  fund, onAdd,
}: {
  fund: BotFundStatus | null
  onAdd: (bot: any) => void
}) {
  const [strategies, setStrategies] = useState<SavedStrategy[]>([])
  const [selectedIdx, setSelectedIdx] = useState(-1)
  const [symbol, setSymbol] = useState('')
  const [interval, setInterval] = useState('15m')
  const [allocation, setAllocation] = useState('')
  const [dataSource, setDataSource] = useState('alpaca-iex')
  const [broker, setBroker] = useState<'alpaca' | 'ibkr'>('alpaca')
  const [direction, setDirection] = useState<'long' | 'short'>('long')
  const [maxSpreadBps, setMaxSpreadBps] = useState('50')
  const [maxDrawdownPct, setMaxDrawdownPct] = useState('')
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)
  // Source: "rule" (strategy rules) or "graph" (node graph)
  const [source, setSource] = useState<'rule' | 'graph'>('rule')
  const [graphList, setGraphList] = useState<GraphList>({ state: 'idle' })
  const [selectedGraphId, setSelectedGraphId] = useState('')
  // Only the newest list request may land (a slow old one must not win).
  const listReq = useRef(0)
  // S36: each chosen graph is fetched once to read its Output Groups.
  const [graphLoads, setGraphLoads] = useState<Record<string, GraphLoad>>({})
  const [selectedGroup, setSelectedGroup] = useState('')
  // A graph rev the bot POST refused as having errors: Create stays off for it.
  const [badGraphRev, setBadGraphRev] = useState<string | null>(null)

  const loadStrategies = () => {
    try {
      const raw = localStorage.getItem(SAVED_KEY)
      if (raw) {
        const parsed = JSON.parse(raw)
        // A non-array here would crash strategies.map in render.
        if (Array.isArray(parsed)) setStrategies(parsed)
      }
    } catch {}
  }

  const loadGraphs = useCallback(async () => {
    const req = ++listReq.current
    setGraphList({ state: 'loading' })
    try {
      const graphs = sortByUpdated(await listGraphs())
      if (req !== listReq.current) return
      setGraphList({ state: 'ready', graphs })
      // The list can change between loads; drop a selection that no longer exists.
      setSelectedGraphId(id => (graphs.some(g => g.id === id) ? id : ''))
    } catch (e) {
      if (req !== listReq.current) return
      setGraphList({ state: 'error', detail: graphErrorDetail(e) })
    }
  }, [])

  // Fetch the list when the Graph source is picked (and on ↻), never per render.
  useEffect(() => {
    if (source === 'graph') void loadGraphs()
  }, [source, loadGraphs])

  const graphs = graphList.state === 'ready' ? graphList.graphs : []
  const selectedGraph = graphs.find(g => g.id === selectedGraphId) ?? null

  const loadGraph = useCallback(async (id: string) => {
    setGraphLoads(m => ({ ...m, [id]: { state: 'loading' } }))
    try {
      const env = await getGraph(id)
      setGraphLoads(m => ({ ...m, [id]: { state: 'ready', env, groups: listGraphGroups(env.graph) } }))
    } catch (e) {
      setGraphLoads(m => ({ ...m, [id]: { state: 'error', detail: graphErrorDetail(e) } }))
    }
  }, [])

  // Fetch a chosen graph once (cached by id). A newer rev in the list refetches it.
  const selectedLoad = selectedGraph ? graphLoads[selectedGraph.id] : undefined
  useEffect(() => {
    if (source !== 'graph' || !selectedGraph) return
    const load = graphLoads[selectedGraph.id]
    if (load == null || (load.state === 'ready' && load.env.rev < selectedGraph.rev)) void loadGraph(selectedGraph.id)
  }, [source, selectedGraph, graphLoads, loadGraph])

  const graphGroups = selectedLoad?.state === 'ready' ? selectedLoad.groups : NO_GROUPS
  const group: GraphGroupInfo | null = graphGroups.find(g => g.name === selectedGroup) ?? graphGroups[0] ?? null
  // Hidden for one group or only the implicit `main` (S36).
  const showGroupSelect = graphGroups.length > 1

  // A new graph (or new groups) preselects the first group.
  useEffect(() => {
    if (graphGroups.length === 0) return
    if (!graphGroups.some(g => g.name === selectedGroup)) setSelectedGroup(graphGroups[0].name)
  }, [graphGroups, selectedGroup])

  // The group fills the interval when it is one a bot can run on.
  useEffect(() => {
    if (source !== 'graph' || !group?.interval) return
    if ((INTERVALS as readonly string[]).includes(group.interval)) setInterval(group.interval)
  }, [source, group?.name, group?.interval])

  // In graph mode the group owns the symbol and (unless it is the implicit
  // group or a regime switch) the direction; the fields show them read-only.
  const graphSymbol = source === 'graph' ? (group?.symbol ?? '') : ''
  const graphDirection = source === 'graph' ? (group?.direction ?? null) : null
  const effectiveSymbol = graphSymbol || symbol
  const graphRevKey = selectedLoad?.state === 'ready' ? `${selectedLoad.env.id}@${selectedLoad.env.rev}` : null
  const graphHasErrors = graphRevKey != null && graphRevKey === badGraphRev

  useEffect(() => {
    loadStrategies()
    // Check for pending spawn from Discovery tab
    try {
      const pending = localStorage.getItem('strategylab-pending-spawn')
      if (pending) {
        localStorage.removeItem('strategylab-pending-spawn')
        const { symbol: pendingSymbol, strategyName } = JSON.parse(pending)
        const raw = localStorage.getItem(SAVED_KEY)
        const strats: SavedStrategy[] = raw ? JSON.parse(raw) : []
        const idx = strats.findIndex(s => s.name === strategyName)
        if (idx >= 0) {
          setStrategies(strats)
          setSelectedIdx(idx)
          setSymbol(pendingSymbol ?? strats[idx].ticker ?? '')
          setInterval(strats[idx].interval ?? '15m')
        } else if (pendingSymbol) {
          setSymbol(pendingSymbol)
        }
      }
    } catch {}
  }, [])

  const onStrategyChange = (idx: number) => {
    setSelectedIdx(idx)
    if (idx >= 0 && strategies[idx]) {
      const s = strategies[idx]
      setSymbol(s.ticker ?? '')
      setInterval(s.interval ?? '15m')
      setDirection((s.direction as 'long' | 'short') ?? 'long')
    }
  }

  const available = fund?.available ?? 0
  const canAdd = fund && fund.bot_fund > 0 && available > 0 && effectiveSymbol && allocation &&
    (source === 'rule' ? selectedIdx >= 0 : selectedGraph != null && selectedLoad?.state === 'ready' && group != null && !graphHasErrors)

  const handleAdd = async () => {
    if (adding) return
    setError('')
    const alloc = parseFloat(allocation)
    if (isNaN(alloc) || alloc <= 0) { setError('Enter a valid allocation'); return }
    if (alloc > available) { setError(`Max available: ${fmtUsd(available)}`); return }
    setAdding(true)
    try {
      if (source === 'graph') {
        // Graph mode (S36): post the saved graph's id, rev and group. The
        // server loads that revision itself (the same path as spawn), so no
        // graph JSON is sent. No buy/sell rules are needed.
        const item = selectedGraph
        if (!item) { setError('Select a graph'); return }
        if (selectedLoad?.state !== 'ready' || !group) { setError('Select a group'); return }
        const env = selectedLoad.env
        try {
          await onAdd({
            strategy_name: group.implicit ? env.name : `${env.name} ▸ ${group.name}`,
            symbol: effectiveSymbol.toUpperCase(),
            interval,
            kind: 'graph',
            graph_id: env.id,
            graph_rev: env.rev,
            graph_group: group.name,
            // Stub rule arrays required by BotConfig schema (empty)
            buy_rules: [],
            sell_rules: [],
            buy_logic: 'AND',
            sell_logic: 'AND',
            long_buy_rules: null,
            long_sell_rules: null,
            short_buy_rules: null,
            short_sell_rules: null,
            allocated_capital: alloc,
            position_size: 1.0,
            slippage_bps: 2.0,
            max_spread_bps: maxSpreadBps ? parseFloat(maxSpreadBps) || null : null,
            drawdown_threshold_pct: maxDrawdownPct ? parseFloat(maxDrawdownPct) || null : null,
            data_source: dataSource,
            // The group owns the direction. A regime_switch group trades both
            // sides and the server takes the mode from the group, so it gets
            // the BotConfig default 'long', never the hidden select (UX-18).
            direction: graphDirection === 'long' || graphDirection === 'short'
              ? graphDirection
              : graphDirection === 'regime_switch' ? 'long' : direction,
            broker,
          })
        } catch (e) {
          // A saved graph with errors: show the first one and keep Create off for this rev.
          const first = firstDiagnosticMessage(e)
          if (first) {
            setBadGraphRev(`${env.id}@${env.rev}`)
            setError(first)
            return
          }
          throw e
        }
      } else {
        const s = strategies[selectedIdx]
        const hasRegime = !!(s.regime && s.regime.enabled)
        await onAdd({
          strategy_name: s.name,
          symbol: symbol.toUpperCase(),
          interval,
          buy_rules: s.buyRules,
          sell_rules: s.sellRules,
          buy_logic: s.buyLogic ?? 'AND',
          sell_logic: s.sellLogic ?? 'AND',
          // Dual rule sets (for regime bots with B23 dual strategies)
          long_buy_rules: s.longBuyRules ?? null,
          long_sell_rules: s.longSellRules ?? null,
          long_buy_logic: s.longBuyLogic ?? 'AND',
          long_sell_logic: s.longSellLogic ?? 'AND',
          short_buy_rules: s.shortBuyRules ?? null,
          short_sell_rules: s.shortSellRules ?? null,
          short_buy_logic: s.shortBuyLogic ?? 'AND',
          short_sell_logic: s.shortSellLogic ?? 'AND',
          allocated_capital: alloc,
          position_size: 1.0,
          stop_loss_pct: typeof s.stopLoss === 'number' ? s.stopLoss : null,
          max_bars_held: typeof s.maxBarsHeld === 'number' ? s.maxBarsHeld : null,
          trailing_stop: s.trailingEnabled ? s.trailingConfig : null,
          dynamic_sizing: s.dynamicSizing ?? null,
          skip_after_stop: s.skipAfterStop ?? null,
          trading_hours: s.tradingHours ?? null,
          slippage_bps: typeof s.slippageBps === 'number' ? s.slippageBps : 2.0,
          max_spread_bps: maxSpreadBps ? parseFloat(maxSpreadBps) || null : null,
          drawdown_threshold_pct: maxDrawdownPct ? parseFloat(maxDrawdownPct) || null : null,
          data_source: dataSource,
          direction: hasRegime ? (s.direction ?? direction) : direction,
          broker,
          regime: hasRegime ? s.regime : null,
        })
      }
      setAllocation('')
    } catch (e) {
      setError(apiErrorDetail(e, 'Failed to add bot'))
    } finally {
      setAdding(false)
    }
  }

  return (
    <div style={{ ...sectionStyle, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* Source radio: Rules | Graph */}
        <span style={{ fontSize: 12, color: '#888' }}>Source:</span>
        {(['rule', 'graph'] as const).map(s => (
          <label key={s} style={{ fontSize: 12, color: source === s ? '#e6edf3' : '#666', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3 }}>
            <input type="radio" value={s} checked={source === s} onChange={() => setSource(s)} style={{ accentColor: '#58a6ff' }} />
            {s === 'rule' ? 'Rules' : 'Graph'}
          </label>
        ))}

        {/* Strategy dropdown (rule mode) or Graph dropdown (graph mode) */}
        {source === 'rule' ? (
          <select
            value={selectedIdx}
            onChange={e => onStrategyChange(Number(e.target.value))}
            onFocus={loadStrategies}
            style={{ ...inputStyle, minWidth: 160 }}
          >
            <option value={-1}>Select strategy…</option>
            {strategies.map((s, i) => (
              <option key={i} value={i}>{s.name}</option>
            ))}
          </select>
        ) : (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ fontSize: 12, color: 'var(--gh-text-muted, #888)' }}>Graph</span>
            <select
              aria-label="Graph"
              value={graphList.state === 'ready' && graphs.length > 0 ? selectedGraphId : ''}
              onChange={e => setSelectedGraphId(e.target.value)}
              disabled={graphList.state !== 'ready' || graphs.length === 0}
              data-testid="addbot-graph-select"
              style={{ ...inputStyle, minWidth: 180 }}
            >
              {graphList.state === 'ready' && graphs.length > 0 ? (
                <>
                  <option value="" disabled>Select a graph…</option>
                  {graphs.map(g => (
                    <option key={g.id} value={g.id}>
                      {g.name} · {g.node_count} node{g.node_count === 1 ? '' : 's'}
                    </option>
                  ))}
                </>
              ) : graphList.state === 'error' ? (
                <option value="">Could not load graphs</option>
              ) : graphList.state === 'ready' ? (
                <option value="">No saved graphs</option>
              ) : (
                <option value="">Loading graphs…</option>
              )}
            </select>
            <button
              type="button"
              title="Refresh graphs"
              aria-label="Refresh graphs"
              data-testid="addbot-graph-refresh"
              onClick={() => void loadGraphs()}
              disabled={graphList.state === 'loading'}
              style={{ ...btnStyle('#1e2530', graphList.state === 'loading'), width: 24, height: 24, padding: 0 }}
            >
              ↻
            </button>
            {selectedGraph && selectedLoad?.state === 'loading' && (
              <select aria-label="Output group" disabled data-testid="addbot-group-select" style={{ ...inputStyle, width: 160 }}>
                <option>loading…</option>
              </select>
            )}
            {selectedGraph && showGroupSelect && (
              <>
                <select
                  aria-label="Output group"
                  value={group?.name ?? ''}
                  onChange={e => setSelectedGroup(e.target.value)}
                  data-testid="addbot-group-select"
                  style={{ ...inputStyle, width: 160 }}
                >
                  {graphGroups.map(g => (
                    <option key={g.name} value={g.name}>{groupOptionLabel(g)}</option>
                  ))}
                </select>
                <span style={{ fontSize: 11, color: 'var(--gh-text-faint, #666)' }}>one bot per group</span>
              </>
            )}
          </span>
        )}

        {/* Ticker */}
        <input
          placeholder={source === 'graph' ? 'from graph' : 'Ticker'}
          aria-label="Ticker"
          value={source === 'graph' ? effectiveSymbol : symbol}
          readOnly={!!graphSymbol}
          title={graphSymbol ? 'Set by the graph group' : undefined}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
          data-testid="addbot-symbol"
          style={{ ...inputStyle, width: 70, ...(graphSymbol ? { color: 'var(--gh-text-faint, #888)' } : {}) }}
        />

        {/* Interval */}
        <select value={interval} onChange={e => setInterval(e.target.value)} style={inputStyle}>
          {INTERVALS.map(v => <option key={v} value={v}>{v}</option>)}
        </select>

        {/* Data source — where OHLCV bars come from for signal evaluation */}
        <select
          value={dataSource}
          onChange={e => setDataSource(e.target.value)}
          style={inputStyle}
          title="Data source — where the bot fetches price bars to evaluate its rules"
        >
          <option value="alpaca-iex">data: IEX</option>
          <option value="alpaca">data: Alpaca SIP</option>
          <option value="ibkr">data: IBKR</option>
          <option value="yahoo">data: Yahoo</option>
        </select>

        {/* Broker (executes orders) */}
        <select
          value={broker}
          onChange={e => setBroker(e.target.value as 'alpaca' | 'ibkr')}
          style={inputStyle}
          title="Broker — which account executes the trades"
        >
          <option value="alpaca">via Alpaca</option>
          <option value="ibkr">via IBKR</option>
        </select>

        {/* Direction */}
        {graphDirection ? (
          <select
            aria-label="Direction"
            value={graphDirection}
            disabled
            title="Set by the graph group"
            data-testid="addbot-direction"
            style={inputStyle}
          >
            <option value="long">Long</option>
            <option value="short">Short</option>
            <option value="regime_switch">Switch</option>
          </select>
        ) : (
          <select aria-label="Direction" value={direction} onChange={e => setDirection(e.target.value as 'long' | 'short')} data-testid="addbot-direction" style={inputStyle}>
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
        )}

        {/* Allocation */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <input
            type="number"
            placeholder="Allocation $"
            value={allocation}
            onChange={e => setAllocation(e.target.value)}
            max={available}
            style={{ ...inputStyle, width: 110 }}
          />
          {fund && fund.bot_fund > 0 && (
            <span style={{ fontSize: 11, color: '#555', whiteSpace: 'nowrap' }}>
              / {fmtUsd(available)}
            </span>
          )}
        </div>

        {/* Max Spread */}
        <input
          type="number"
          placeholder="Max Spread bps"
          value={maxSpreadBps}
          min={0}
          onChange={e => setMaxSpreadBps(e.target.value)}
          style={{ ...inputStyle, width: 70 }}
          title="Skip entries when bid/ask spread exceeds this (bps). Empty = disabled."
        />

        {/* Max Drawdown */}
        <input
          type="number"
          placeholder="Max DD %"
          value={maxDrawdownPct}
          min={0}
          step={0.1}
          onChange={e => setMaxDrawdownPct(e.target.value)}
          style={{ ...inputStyle, width: 70 }}
          title="Auto-pause bot when cumulative drawdown from peak exceeds this % of allocated capital. Empty = disabled."
        />

        <button
          onClick={handleAdd}
          disabled={!canAdd || adding}
          title={
            source === 'graph' && graphHasErrors ? "Fix the graph's errors first"
              : source === 'graph' && graphList.state === 'ready' && graphs.length > 0 && !selectedGraph ? 'Select a graph'
                : undefined
          }
          data-testid="addbot-add"
          style={btnStyle('#1e3a5f', !canAdd || adding)}
        >
          {adding ? 'Adding…' : '+ Add Bot'}
        </button>
      </div>

      {source === 'graph' && (
        <div
          data-testid="addbot-graph-help"
          style={{
            fontSize: 11,
            color: graphList.state === 'error' ? 'var(--gh-red, #ef5350)' : 'var(--gh-text-muted, #888)',
            minHeight: 16,
          }}
        >
          {/* Only the error and empty texts are live: the selected graph's
              "updated N min ago" ticks every minute and must not be re-read. */}
          <span aria-live="polite">
            {graphList.state === 'error' ? (
              <>
                {graphList.detail} ·{' '}
                <button
                  type="button"
                  onClick={() => void loadGraphs()}
                  style={{ background: 'none', border: 'none', padding: 0, color: 'inherit', font: 'inherit', textDecoration: 'underline', cursor: 'pointer' }}
                >
                  Retry
                </button>
              </>
            ) : graphList.state === 'ready' && graphs.length === 0 ? (
              'Build one in the Node Editor and save it.'
            ) : null}
          </span>
          {graphList.state !== 'error' && graphs.length > 0 && selectedGraph && <SelectedGraphLine graph={selectedGraph} />}
          {selectedGraph && selectedLoad?.state === 'error' && (
            <div role="alert" data-testid="addbot-graph-load-error" style={{ color: 'var(--gh-red, #ef5350)' }}>
              {selectedLoad.detail} ·{' '}
              <button
                type="button"
                onClick={() => void loadGraph(selectedGraph.id)}
                style={{ background: 'none', border: 'none', padding: 0, color: 'inherit', font: 'inherit', textDecoration: 'underline', cursor: 'pointer' }}
              >
                Retry
              </button>
            </div>
          )}
        </div>
      )}

      {error && <span style={{ color: '#ef5350', fontSize: 12 }}>{error}</span>}
    </div>
  )
}
