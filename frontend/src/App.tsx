import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import type { BacktestResult, IndicatorInstance, DataSource, StrategyRequest, DatePreset } from './shared/types'
import { DEFAULT_INDICATORS } from './shared/types/indicators'
import type { IChartApi } from 'lightweight-charts'
import { useOHLCV, useInstanceIndicators } from './shared/hooks/useOHLCV'
import { getCoarserIntervals } from './shared/utils/intervals'
import Sidebar from './features/sidebar/Sidebar'
import Chart from './features/chart/Chart'
import StrategyBuilder from './features/strategy/StrategyBuilder'
import Results, { type ResultsTab } from './features/strategy/Results'
import StrategyComparison from './features/strategy/StrategyComparison'
import PaperTrading from './features/trading/PaperTrading'
import Discovery from './features/discovery/Discovery'
import WatchlistPanel from './features/watchlist/WatchlistPanel'
import { useTimezone, tzLabel } from './shared/utils/time'

type AppTab = 'chart' | 'trading' | 'discovery'


const STORAGE_KEY = 'strategylab-settings'
const BACKTEST_CACHE_KEY = 'strategylab-last-backtest'
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

const saved = loadSettings()

// Restore last backtest result if the settings (ticker/dates/interval) still match
const _cachedBacktest = (() => {
  const cache = loadBacktestCache()
  if (!cache?.request) return null
  const r = cache.request
  if (r.ticker === (saved?.ticker ?? 'AAPL') &&
      r.start === (saved?.start ?? oneYearAgo) &&
      r.end === (saved?.end ?? today) &&
      r.interval === (saved?.interval ?? '1d')) {
    return cache
  }
  return null
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
  const [macroBucket, setMacroBucket] = useState<string | null>(null)
  const [showBaseline, setShowBaseline] = useState(false)
  const [logScale, setLogScale] = useState(false)
  const [compareMode, setCompareMode] = useState(false)
  const [activeTab, setActiveTab] = useState<AppTab>(() => {
    const s = localStorage.getItem('activeTab')
    return s === 'chart' || s === 'trading' || s === 'discovery' ? s : 'chart'
  })
  const [mainChart, setMainChart] = useState<IChartApi | null>(null)
  const [chartEnabled, setChartEnabled] = useState(true)
  const [datePreset, setDatePreset] = useState<DatePreset>((saved?.datePreset as DatePreset) ?? 'Y')
  const [viewInterval, setViewInterval] = useState(saved?.viewInterval ?? interval)
  const intervalRef = useRef(interval)

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
        localStorage.setItem(BACKTEST_CACHE_KEY, JSON.stringify({ result: backtestResult, request: lastRequest }))
      } catch {} // Quota exceeded — silently skip
    } else {
      localStorage.removeItem(BACKTEST_CACHE_KEY)
    }
  }, [backtestResult, lastRequest])

  const chartInterval = chartEnabled ? viewInterval : interval
  const { data: ohlcv = EMPTY_OHLCV, refetch: refetchOhlcv } = useOHLCV(ticker, start, end, chartInterval, dataSource, extendedHours)
  const { data: spyData, refetch: refetchSpy } = useOHLCV('SPY', start, end, chartInterval, dataSource, extendedHours, chartEnabled && showSpy)
  const { data: qqqData, refetch: refetchQqq } = useOHLCV('QQQ', start, end, chartInterval, dataSource, extendedHours, chartEnabled && showQqq)

  const { data: instanceData = {}, refetch: refetchIndicators } = useInstanceIndicators(
    ticker, start, end, chartInterval, chartEnabled ? indicators : [], dataSource, extendedHours,
  )

  const refreshChart = useCallback(() => {
    refetchOhlcv(); refetchIndicators(); refetchSpy(); refetchQqq()
  }, [refetchOhlcv, refetchIndicators, refetchSpy, refetchQqq])

  const trades = useMemo(() => backtestResult?.trades ?? [], [backtestResult])
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
              {chartEnabled && viewIntervalOptions.length > 1 && (
                <select
                  value={viewInterval}
                  onChange={e => setViewInterval(e.target.value)}
                  style={{ background: '#21262d', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px', fontSize: 12 }}
                  title="Chart display interval"
                >
                  {viewIntervalOptions.map(o => (
                    <option key={o.value} value={o.value}>{o.value === interval ? o.label : `View ${o.label}`}</option>
                  ))}
                </select>
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
            <Panel defaultSize="14%" minSize="8%">
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
              />
            </Panel>

            <Separator className="resize-handle-v" />

            {/* CENTER COLUMN */}
            <Panel defaultSize="66%" minSize="30%">
              <div style={{ height: '100%', overflow: 'hidden' }}>
                <Group orientation="vertical" style={{ height: '100%' }}>

                  {/* CHART */}
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
                          trades={trades}
                          emaOverlays={emaOverlays}
                          ruleSignals={ruleSignals}
                          regimeSeries={regimeSeries}
                          viewInterval={viewInterval}
                          backtestInterval={interval}
                          onChartReady={setMainChart}
                        />
                      ) : (
                        <div style={styles.empty}>Loading {ticker}...</div>
                      )}
                    </div>
                  </Panel>

                  <Separator className="resize-handle-h" />

                  {/* BOTTOM PANE: Strategy rules + Results */}
                  <Panel defaultSize="50%" minSize="30%" collapsible>
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
                            ticker={ticker}
                            start={start}
                            end={end}
                            interval={interval}
                            onResult={(result, req) => {
                              setBacktestResult(result)
                              if (req) setLastRequest(req)
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
            <Panel defaultSize="20%" minSize="12%" collapsible>
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
  rightPanel: {
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    background: 'var(--bg-main)',
    borderLeft: '1px solid var(--border-light)',
    overflow: 'hidden',
  },
}
