import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import type { BacktestResult, IndicatorInstance, DataSource, StrategyRequest, DatePreset } from './shared/types'
import { requestSignature } from './shared/types/requestSignature'
import { DEFAULT_INDICATORS } from './shared/types/indicators'
import type { IChartApi } from 'lightweight-charts'
import { useOHLCV, useInstanceIndicators } from './shared/hooks/useOHLCV'
import { getCoarserIntervals } from './shared/utils/intervals'
import Sidebar from './features/sidebar/Sidebar'
import Chart from './features/chart/Chart'
import ChartSkeleton from './features/chart/ChartSkeleton'
import StrategyBuilder, { type StrategyBuilderHandle } from './features/strategy/StrategyBuilder'
import Results, { type ResultsTab } from './features/strategy/Results'
import StrategyComparison from './features/strategy/StrategyComparison'
import PaperTrading from './features/trading/PaperTrading'
import Discovery from './features/discovery/Discovery'
import WatchlistPanel from './features/watchlist/WatchlistPanel'
import { useTimezone, tzLabel } from './shared/utils/time'
import { seedFromLocalStorageIfAny } from './shared/utils/seedFromLocalStorage'

type AppTab = 'chart' | 'trading' | 'discovery'


const STORAGE_KEY = 'strategylab-settings'
const BACKTEST_CACHE_KEY = 'strategylab-last-backtest'
const CHART_COLLAPSED_KEY = 'strategylab-chart-collapsed'
const EMPTY_OHLCV: never[] = []
const today = new Date().toISOString().slice(0, 10)
const oneYearAgo = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)

function loadSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function loadBacktestCache(): { result: BacktestResult; request: StrategyRequest } | null {
  try {
    const raw = localStorage.getItem(BACKTEST_CACHE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function loadStrategyCache(): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem('strategylab-strategy')
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

const saved = loadSettings()

// Restore last backtest result if the settings (ticker/dates/interval) still match
// and the rule/logic/regime state hasn't changed since the backtest was run.
const _cachedBacktest = (() => {
  const cache = loadBacktestCache()
  if (!cache?.request) return null
  const strat = loadStrategyCache()
  // Build a StrategyRequest-shaped object from current saved settings + strategy
  // cache so requestSignature can compare it against the cached request in one call.
  // Mirror runBacktest's conditional: regime + long_*/short_* only populated when
  // regime.enabled. Otherwise the cached request omits those keys (undefined → null
  // in the signature) while a populated `current` would have arrays — silent mismatch.
  const regimeOn = !!(strat?.regime as { enabled?: boolean } | undefined)?.enabled
  const current: StrategyRequest = {
    ticker: saved?.ticker ?? 'AAPL',
    start: saved?.start ?? oneYearAgo,
    end: saved?.end ?? today,
    interval: saved?.interval ?? '1d',
    buy_rules: (strat?.buyRules ?? null) as StrategyRequest['buy_rules'],
    sell_rules: (strat?.sellRules ?? null) as StrategyRequest['sell_rules'],
    buy_logic: (strat?.buyLogic ?? null) as StrategyRequest['buy_logic'],
    sell_logic: (strat?.sellLogic ?? null) as StrategyRequest['sell_logic'],
    long_buy_rules: regimeOn ? (strat?.longBuyRules ?? null) as StrategyRequest['long_buy_rules'] : undefined,
    long_sell_rules: regimeOn ? (strat?.longSellRules ?? null) as StrategyRequest['long_sell_rules'] : undefined,
    long_buy_logic: regimeOn ? (strat?.longBuyLogic ?? null) as StrategyRequest['long_buy_logic'] : undefined,
    long_sell_logic: regimeOn ? (strat?.longSellLogic ?? null) as StrategyRequest['long_sell_logic'] : undefined,
    short_buy_rules: regimeOn ? (strat?.shortBuyRules ?? null) as StrategyRequest['short_buy_rules'] : undefined,
    short_sell_rules: regimeOn ? (strat?.shortSellRules ?? null) as StrategyRequest['short_sell_rules'] : undefined,
    short_buy_logic: regimeOn ? (strat?.shortBuyLogic ?? null) as StrategyRequest['short_buy_logic'] : undefined,
    short_sell_logic: regimeOn ? (strat?.shortSellLogic ?? null) as StrategyRequest['short_sell_logic'] : undefined,
    regime: regimeOn ? (strat?.regime ?? null) as StrategyRequest['regime'] : undefined,
    // required non-compared fields — defaults; only the signature fields above matter
    initial_capital: 0,
    position_size: 0,
    source: 'yahoo',
  }
  if (requestSignature(cache.request) !== requestSignature(current)) return null
  return cache
})()


export default function App() {
  const [tzMode, setTzMode] = useTimezone()
  const [ticker, setTicker] = useState(saved?.ticker ?? 'AAPL')
  const [start, setStart] = useState(saved?.start ?? oneYearAgo)
  const [end, setEnd] = useState(saved?.end ?? today)
  const [interval, setInterval] = useState(saved?.interval ?? '1d')
  const [indicators, setIndicators] = useState<IndicatorInstance[]>(saved?.indicators ?? DEFAULT_INDICATORS)
  const [showSpy, setShowSpy] = useState<boolean>(saved?.showSpy ?? false)
  const [showQqq, setShowQqq] = useState<boolean>(saved?.showQqq ?? false)
  const [dataSource, setDataSource] = useState<DataSource>((saved?.dataSource as DataSource) ?? 'yahoo')
  const [extendedHours, setExtendedHours] = useState<boolean>(saved?.extendedHours ?? false)
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(_cachedBacktest?.result ?? null)
  const [lastRequest, setLastRequest] = useState<StrategyRequest | null>(_cachedBacktest?.request ?? null)
  const [resultsTab, setResultsTab] = useState<ResultsTab>('summary')
  const [sweepInit, setSweepInit] = useState<{ path: string; centerVal: number } | null>(null)
  const [macroBucket, setMacroBucket] = useState<string | null>(null)
  const [showBaseline, setShowBaseline] = useState(false)
  const [logScale, setLogScale] = useState(false)
  const [compareMode, setCompareMode] = useState(false)
  const [activeTab, setActiveTab] = useState<AppTab>(() => {
    const s = localStorage.getItem('activeTab')
    return s === 'chart' || s === 'trading' || s === 'discovery' ? s : 'chart'
  })
  const [mainChart, setMainChart] = useState<IChartApi | null>(null)
  // F227 — ref into StrategyBuilder for Apply-from-Optimizer/WFA
  const strategyBuilderRef = useRef<StrategyBuilderHandle>(null)
  const [chartEnabled, setChartEnabled] = useState(true)
  // F248 — collapsible chart panel; persisted to localStorage
  const [chartCollapsed, setChartCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(CHART_COLLAPSED_KEY) === 'true' } catch { return false }
  })
  // F244 — narrow rails below 1440 px (evaluated once on mount; viewport changes during use are rare)
  const narrowRails = useMemo(() => window.innerWidth < 1440, [])
  const [datePreset, setDatePreset] = useState<DatePreset>((saved?.datePreset as DatePreset) ?? 'Y')
  const [viewInterval, setViewInterval] = useState(saved?.viewInterval ?? interval)
  const [isAggOpen, setIsAggOpen] = useState(false)
  const intervalRef = useRef(interval)

  // One-shot: push any pre-existing localStorage data to the backend seed
  // endpoints (no-op if the server already has data). Runs before other queries.
  useEffect(() => {
    seedFromLocalStorageIfAny()
  }, [])

  useEffect(() => {
    if (interval !== intervalRef.current) {
      setViewInterval(interval)
      intervalRef.current = interval
    }
  }, [interval])

  const viewIntervalOptions = useMemo(() => getCoarserIntervals(interval), [interval])

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      ticker, start, end, interval, indicators, showSpy, showQqq, dataSource, extendedHours, datePreset, viewInterval,
    }))
  }, [ticker, start, end, interval, indicators, showSpy, showQqq, dataSource, extendedHours, datePreset, viewInterval])

  useEffect(() => {
    if (backtestResult && lastRequest) {
      try {
        // Persist the FULL request — downstream consumers (WFA / Optimizer panels)
        // re-send lastRequest to the backend and need every field, not just the
        // 17 used by requestSignature(). The signature is the read predicate,
        // not a storage projection.
        localStorage.setItem(BACKTEST_CACHE_KEY, JSON.stringify({ result: backtestResult, request: lastRequest }))
      } catch {} // Quota exceeded — silently skip
    } else {
      localStorage.removeItem(BACKTEST_CACHE_KEY)
    }
  }, [backtestResult, lastRequest])

  useEffect(() => {
    try { localStorage.setItem(CHART_COLLAPSED_KEY, String(chartCollapsed)) } catch {}
  }, [chartCollapsed])

  const chartInterval = chartEnabled ? viewInterval : interval
  const { data: ohlcv = EMPTY_OHLCV, isLoading: ohlcvLoading, isFetching: ohlcvFetching, isError: ohlcvError, refetch: refetchOhlcv } = useOHLCV(ticker, start, end, chartInterval, dataSource, extendedHours)
  const { data: spyData, refetch: refetchSpy } = useOHLCV('SPY', start, end, chartInterval, dataSource, extendedHours, chartEnabled && showSpy)
  const { data: qqqData, refetch: refetchQqq } = useOHLCV('QQQ', start, end, chartInterval, dataSource, extendedHours, chartEnabled && showQqq)

  const { data: instanceData = {}, refetch: refetchIndicators, isLoading: instanceLoading, loadingByInstance, isError: instanceError, errorMessage: instanceErrorMessage } = useInstanceIndicators(
    ticker, start, end, interval, chartEnabled ? indicators : [], dataSource, extendedHours, viewInterval,
  )

  const refreshChart = useCallback(() => {
    refetchOhlcv(); refetchIndicators(); refetchSpy(); refetchQqq()
  }, [refetchOhlcv, refetchIndicators, refetchSpy, refetchQqq])

  const trades = useMemo(() => backtestResult?.trades ?? [], [backtestResult])
  const mainTimestamps = useMemo(() => ohlcv.map(d => d.time), [ohlcv])
  const emaOverlays = backtestResult?.ema_overlays
  const ruleSignals = backtestResult?.rule_signals
  const regimeSeries = backtestResult?.regime_series

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Header */}
      <header style={styles.header}>
        <span style={styles.logo}>StrategyLab</span>
        <div style={styles.tabs}>
          {(['chart', 'trading', 'discovery'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => { setActiveTab(tab); localStorage.setItem('activeTab', tab) }}
              style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
            >
              {tab === 'chart' ? 'Chart' : tab === 'trading' ? 'Live Trading' : 'Discovery'}
            </button>
          ))}
        </div>
        <span style={{ color: '#8b949e', fontSize: 13, display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            onClick={() => setTzMode(tzMode === 'ET' ? 'local' : 'ET')}
            style={{ ...styles.chartToggleBtn, color: tzMode === 'local' ? '#58a6ff' : '#8b949e' }}
            title={tzMode === 'ET' ? 'Showing Eastern Time — click for local' : 'Showing local time — click for Eastern'}
          >
            {tzLabel()}
          </button>
          {ticker} &nbsp;·&nbsp; {start} → {end}
          {activeTab === 'chart' && (
            <>
              <button onClick={refreshChart} style={styles.chartToggleBtn} title="Reload chart data">
                ↻
              </button>
              <button onClick={() => setChartEnabled(c => !c)} style={{ ...styles.chartToggleBtn, opacity: chartEnabled ? 0.5 : 1 }}>
                {chartEnabled ? 'Disable Chart' : 'Enable Chart'}
              </button>
              <button
                onClick={() => setChartCollapsed(c => !c)}
                style={{ ...styles.chartToggleBtn, opacity: chartCollapsed ? 1 : 0.5 }}
                title={chartCollapsed ? 'Expand chart panel' : 'Collapse chart panel'}
                aria-label={chartCollapsed ? 'Expand chart panel' : 'Collapse chart panel'}
              >
                {chartCollapsed ? '▸ Show Chart' : '◂ Hide Chart'}
              </button>
              {/* F235 — sticky metrics strip after button cluster */}
              {backtestResult && (() => {
                const s = backtestResult.summary
                const ret = s.total_return_pct != null ? (s.total_return_pct >= 0 ? '+' : '') + s.total_return_pct.toFixed(1) + '%' : '—'
                const sharpe = s.sharpe_ratio != null ? s.sharpe_ratio.toFixed(2) : '—'
                const dd = s.max_drawdown_pct != null ? '−' + Math.abs(s.max_drawdown_pct).toFixed(1) + '%' : '—'
                const ntrades = s.num_trades ?? 0
                return (
                  <span style={styles.metricsStrip} title={`${ticker} ${interval} backtest summary`}>
                    {ticker} {interval} · {ntrades} trade{ntrades !== 1 ? 's' : ''} · {ret} · Sharpe {sharpe} · MaxDD {dd}
                  </span>
                )
              })()}
              {chartEnabled && viewIntervalOptions.length > 1 && (
                viewInterval === interval && !isAggOpen ? (
                  <button
                    onClick={() => setIsAggOpen(true)}
                    style={{ background: 'none', border: '1px solid #30363d', borderRadius: 4, color: '#8b949e', cursor: 'pointer', fontSize: 11, padding: '2px 6px' }}
                    title="Aggregate chart to a coarser interval"
                  >Aggregate ▾</button>
                ) : (
                  <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    {viewInterval !== interval && <span style={{ fontSize: 11, color: '#8b949e' }}>Aggregate:</span>}
                    <select
                      value={viewInterval}
                      onChange={e => { setViewInterval(e.target.value); if (e.target.value === interval) setIsAggOpen(false); }}
                      onBlur={() => { if (viewInterval === interval) setIsAggOpen(false); }}
                      style={{ background: '#21262d', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px', fontSize: 12 }}
                      title="Chart display interval"
                      autoFocus={isAggOpen && viewInterval === interval}
                    >
                      {viewIntervalOptions.map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </span>
                )
              )}
            </>
          )}
        </span>
      </header>

      {/* Main area — both tabs stay mounted, toggle via display to preserve state */}
      <div style={{ height: 'calc(100vh - 56px)' }}>
        <div style={{ height: '100%', display: activeTab === 'chart' ? 'block' : 'none' }}>
          <Group orientation="horizontal" style={{ height: '100%' }}>

            {/* LEFT SIDEBAR */}
            <Panel defaultSize={narrowRails ? '11%' : '14%'} minSize="8%">
              <Sidebar
                ticker={ticker}
                start={start}
                end={end}
                interval={interval}
                indicators={indicators}
                onIndicatorsChange={setIndicators}
                showSpy={showSpy}
                showQqq={showQqq}
                onTickerChange={t => { setTicker(t); setBacktestResult(null) }}
                onStartChange={d => { if (d > end) { setStart(end); setEnd(d) } else { setStart(d) }; setBacktestResult(null) }}
                onEndChange={d => { if (d < start) { setEnd(start); setStart(d) } else { setEnd(d) }; setBacktestResult(null) }}
                onIntervalChange={v => { setInterval(v); setBacktestResult(null) }}
                onToggleSpy={() => setShowSpy(v => !v)}
                onToggleQqq={() => setShowQqq(v => !v)}
                dataSource={dataSource}
                onDataSourceChange={setDataSource}
                extendedHours={extendedHours}
                onExtendedHoursChange={setExtendedHours}
                datePreset={datePreset}
                onDatePresetChange={v => { setDatePreset(v); setBacktestResult(null) }}
                dataError={ohlcvError}
              />
            </Panel>

            <Separator className="resize-handle-v" />

            {/* CENTER COLUMN */}
            <Panel defaultSize="66%" minSize="30%">
              <div style={{ height: '100%', overflow: 'hidden' }}>
                {/* F248: keying the Group on `chartCollapsed` forces a fresh
                     layout when toggling — chart Panel + Separator are
                     conditionally rendered, so collapsing actually frees
                     vertical space for the Results pane below. React's
                     unmount/remount of <Chart/> is the safe path (per the
                     F-UX29-full notes in the plan); the inner autoSize
                     teardown was hardened earlier. */}
                <Group orientation="vertical" key={chartCollapsed ? 'rc-only' : 'split'} style={{ height: '100%' }}>

                  {!chartCollapsed && (<>
                  <Panel defaultSize="50%" minSize="15%">
                    <div className="panel-fill">
                      {!chartEnabled ? (
                        <div style={styles.chartDisabled}>
                          <span style={{ color: '#8b949e', fontSize: 12 }}>Chart disabled</span>
                          <button onClick={() => setChartEnabled(true)} style={styles.chartToggleBtn}>Enable</button>
                        </div>
                      ) : ohlcv.length > 0 ? (
                        <Chart
                          data={ohlcv}
                          spyData={showSpy ? (spyData ?? []) : undefined}
                          qqqData={showQqq ? (qqqData ?? []) : undefined}
                          showSpy={showSpy}
                          showQqq={showQqq}
                          indicators={indicators}
                          instanceData={instanceData}
                          instanceLoading={instanceLoading}
                          loadingByInstance={loadingByInstance}
                          instanceError={instanceError}
                          instanceErrorMessage={instanceErrorMessage}
                          onRetryIndicators={refetchIndicators}
                          trades={trades}
                          emaOverlays={emaOverlays}
                          ruleSignals={ruleSignals}
                          regimeSeries={regimeSeries}
                          viewInterval={viewInterval}
                          backtestInterval={interval}
                          onChartReady={setMainChart}
                          ticker={ticker}
                          interval={chartInterval}
                          from={start}
                          to={end}
                        />
                      ) : (ohlcvLoading || ohlcvFetching) ? (
                        <ChartSkeleton ticker={ticker} />
                      ) : ohlcvError ? (
                        <div style={styles.empty}>Failed to load {ticker}</div>
                      ) : (
                        <div style={styles.empty}>No data for {ticker}</div>
                      )}
                    </div>
                  </Panel>

                  <Separator className="resize-handle-h" />
                  </>)}

                  {/* BOTTOM PANE: Strategy rules + Results */}
                  <Panel defaultSize={chartCollapsed ? '100%' : '50%'} minSize="30%" collapsible>
                    <div style={{ height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column', background: 'var(--bg-main)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', padding: '4px 8px', borderBottom: '1px solid #21262d', background: '#0d1117', flexShrink: 0 }}>
                        <button
                          onClick={() => setCompareMode(m => !m)}
                          style={{
                            fontSize: 11, padding: '3px 10px', borderRadius: 4,
                            background: compareMode ? 'rgba(88,166,255,0.15)' : '#21262d',
                            color: compareMode ? '#58a6ff' : '#8b949e',
                            border: compareMode ? '1px solid #58a6ff55' : '1px solid #30363d',
                            cursor: 'pointer', fontWeight: 600,
                          }}
                          title="Compare saved strategies side-by-side"
                        >
                          ⇄ Compare
                        </button>
                      </div>
                      {compareMode ? (
                        <StrategyComparison
                          ticker={ticker}
                          start={start}
                          end={end}
                          interval={interval}
                          dataSource={dataSource}
                          extendedHours={extendedHours}
                        />
                      ) : (
                        <>
                          <StrategyBuilder
                            ref={strategyBuilderRef}
                            ticker={ticker}
                            start={start}
                            end={end}
                            interval={interval}
                            onResult={(result, req) => {
                              setBacktestResult(result)
                              if (req) setLastRequest(req)
                            }}
                            onSweep={(path, centerVal) => {
                              setSweepInit({ path, centerVal })
                              setResultsTab('sensitivity')
                            }}
                            dataSource={dataSource}
                            settingsPortalId="strategy-settings-portal"
                            extendedHours={extendedHours}
                          />
                          {backtestResult && (
                            <Results
                              result={backtestResult}
                              mainChart={mainChart}
                              activeTab={resultsTab}
                              onTabChange={setResultsTab}
                              bucket={macroBucket}
                              onBucketChange={setMacroBucket}
                              lastRequest={lastRequest}
                              showBaseline={showBaseline}
                              onShowBaselineChange={setShowBaseline}
                              logScale={logScale}
                              onLogScaleChange={setLogScale}
                              viewInterval={viewInterval}
                              backtestInterval={interval}
                              sweepInit={sweepInit}
                              onSweepConsumed={() => setSweepInit(null)}
                              mainTimestamps={mainTimestamps}
                              onApplyParams={(updatedReq) => {
                                strategyBuilderRef.current?.applyStrategyRequest(updatedReq)
                              }}
                              onRunBacktest={() => {
                                strategyBuilderRef.current?.triggerRun()
                              }}
                            />
                          )}
                        </>
                      )}
                    </div>
                  </Panel>

                </Group>
              </div>
            </Panel>

            <Separator className="resize-handle-v" />

            {/* RIGHT SIDEBAR: Watchlist + Settings */}
            <Panel defaultSize={narrowRails ? '17%' : '20%'} minSize="12%" collapsible>
              <div style={styles.rightPanel}>
                <Group orientation="vertical" style={{ height: '100%' }}>
                  <Panel defaultSize="30%" minSize="15%">
                    <WatchlistPanel
                      currentSymbol={ticker}
                      onSymbolClick={t => { setTicker(t); setBacktestResult(null) }}
                    />
                  </Panel>
                  <Separator className="resize-handle-h" />
                  <Panel defaultSize="70%" minSize="20%">
                    <div id="strategy-settings-portal" style={{ height: '100%', overflow: 'hidden' }} />
                  </Panel>
                </Group>
              </div>
            </Panel>

          </Group>
        </div>
        <div style={{ height: '100%', display: activeTab === 'trading' ? 'block' : 'none' }}>
          <PaperTrading />
        </div>
        <div style={{ height: '100%', display: activeTab === 'discovery' ? 'block' : 'none' }}>
          <Discovery onSpawnBot={(symbol, strategyName) => {
            localStorage.setItem('strategylab-pending-spawn', JSON.stringify({ symbol, strategyName }))
            setActiveTab('trading')
            localStorage.setItem('activeTab', 'trading')
          }} />
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: 'flex', alignItems: 'center', gap: 16,
    padding: '0 20px', height: 56,
    background: 'var(--bg-panel)', borderBottom: '1px solid var(--border-light)',
    flexShrink: 0, boxShadow: 'var(--shadow-sm)', zIndex: 10,
  },
  logo: { fontWeight: 800, fontSize: 18, color: 'var(--accent-primary)', letterSpacing: '-0.03em' },
  tabs: { display: 'flex', gap: 4, background: 'var(--bg-input)', padding: 4, borderRadius: 'var(--radius-md)' },
  tab: {
    fontSize: 13, padding: '6px 16px', borderRadius: 'var(--radius-sm)',
    background: 'transparent', color: 'var(--text-secondary)',
    cursor: 'pointer', fontWeight: 600, transition: 'all 0.2s ease', border: 'none',
  },
  tabActive: {
    background: 'var(--bg-panel-hover)', color: 'var(--text-primary)',
    boxShadow: 'var(--shadow-sm)',
  },
  empty: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', fontSize: 14 },
  chartDisabled: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 },
  chartToggleBtn: { fontSize: 11, padding: '3px 10px', borderRadius: 4, background: '#21262d', color: '#8b949e', border: '1px solid #30363d', cursor: 'pointer' },
  chevronBtn: { fontSize: 14, width: 14, height: 14, lineHeight: '14px', padding: 0, background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  rightPanel: {
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    background: 'var(--bg-main)',
    borderLeft: '1px solid var(--border-light)',
    overflow: 'hidden',
  },
  metricsStrip: {
    fontSize: 11, color: 'var(--text-secondary)',
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
    padding: '0 6px', maxWidth: 380,
  },
}
