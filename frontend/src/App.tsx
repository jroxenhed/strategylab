import { useState, useCallback, useEffect, useMemo } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import type { BacktestResult, IndicatorKey, DataSource } from './shared/types'
import type { IChartApi } from 'lightweight-charts'
import { useOHLCV, useIndicators } from './shared/hooks/useOHLCV'
import Sidebar from './features/sidebar/Sidebar'
import Chart from './features/chart/Chart'
import StrategyBuilder from './features/strategy/StrategyBuilder'
import Results from './features/strategy/Results'
import PaperTrading from './features/trading/PaperTrading'

type AppTab = 'chart' | 'trading'

const STORAGE_KEY = 'strategylab-settings'
const EMPTY_OHLCV: never[] = []
const EMPTY_INDICATORS: Record<string, never[]> = {}
const today = new Date().toISOString().slice(0, 10)
const oneYearAgo = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)

function loadSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

const saved = loadSettings()

export default function App() {
  const [ticker, setTicker] = useState(saved?.ticker ?? 'AAPL')
  const [start, setStart] = useState(saved?.start ?? oneYearAgo)
  const [end, setEnd] = useState(saved?.end ?? today)
  const [interval, setInterval] = useState(saved?.interval ?? '1d')
  const [activeIndicators, setActiveIndicators] = useState<IndicatorKey[]>(saved?.activeIndicators ?? ['macd', 'rsi'])
  const [showSpy, setShowSpy] = useState(saved?.showSpy ?? false)
  const [showQqq, setShowQqq] = useState(saved?.showQqq ?? false)
  const [dataSource, setDataSource] = useState<DataSource>((saved?.dataSource as DataSource) ?? 'yahoo')
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null)
  const [activeTab, setActiveTab] = useState<AppTab>('chart')
  const [mainChart, setMainChart] = useState<IChartApi | null>(null)
  const [chartEnabled, setChartEnabled] = useState(true)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      ticker, start, end, interval, activeIndicators, showSpy, showQqq, dataSource,
    }))
  }, [ticker, start, end, interval, activeIndicators, showSpy, showQqq, dataSource])

  const { data: ohlcv = EMPTY_OHLCV } = useOHLCV(ticker, start, end, interval, dataSource)
  const { data: spyData } = useOHLCV('SPY', start, end, interval, dataSource)
  const { data: qqqData } = useOHLCV('QQQ', start, end, interval, dataSource)

  const indicatorKeys = activeIndicators.filter(k => k !== 'volume')
  const { data: indicatorData = EMPTY_INDICATORS } = useIndicators(ticker, start, end, interval, indicatorKeys, dataSource)

  const toggleIndicator = useCallback((key: IndicatorKey) => {
    setActiveIndicators(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    )
  }, [])

  const trades = useMemo(() => backtestResult?.trades ?? [], [backtestResult])
  const emaOverlays = backtestResult?.ema_overlays

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Header */}
      <header style={styles.header}>
        <span style={styles.logo}>StrategyLab</span>
        <div style={styles.tabs}>
          {(['chart', 'trading'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
            >
              {tab === 'chart' ? 'Chart' : 'Paper Trading'}
            </button>
          ))}
        </div>
        <span style={{ color: '#8b949e', fontSize: 13, display: 'flex', alignItems: 'center', gap: 12 }}>
          {ticker} &nbsp;·&nbsp; {start} → {end}
          {activeTab === 'chart' && (
            <button onClick={() => setChartEnabled(c => !c)} style={{ ...styles.chartToggleBtn, opacity: chartEnabled ? 0.5 : 1 }}>
              {chartEnabled ? 'Disable Chart' : 'Enable Chart'}
            </button>
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
                activeIndicators={activeIndicators}
                showSpy={showSpy}
                showQqq={showQqq}
                onTickerChange={t => { setTicker(t); setBacktestResult(null) }}
                onStartChange={d => { if (d > end) { setStart(end); setEnd(d) } else { setStart(d) } }}
                onEndChange={d => { if (d < start) { setEnd(start); setStart(d) } else { setEnd(d) } }}
                onIntervalChange={setInterval}
                onToggleIndicator={toggleIndicator}
                onToggleSpy={() => setShowSpy(v => !v)}
                onToggleQqq={() => setShowQqq(v => !v)}
                dataSource={dataSource}
                onDataSourceChange={setDataSource}
              />
            </Panel>

            <Separator className="resize-handle-v" />

            {/* CENTER COLUMN */}
            <Panel defaultSize="66%" minSize="30%">
              <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

                {/* CHART — takes remaining space */}
                <div style={{ flex: 1, minHeight: 0 }}>
                  <div className="panel-fill">
                    {!chartEnabled ? (
                      <div style={styles.chartDisabled}>
                        <span style={{ color: '#8b949e', fontSize: 12 }}>Chart disabled</span>
                        <button onClick={() => setChartEnabled(true)} style={styles.chartToggleBtn}>Enable</button>
                      </div>
                    ) : ohlcv.length > 0 ? (
                      <Chart
                        ticker={ticker}
                        data={ohlcv}
                        spyData={showSpy ? (spyData ?? []) : undefined}
                        qqqData={showQqq ? (qqqData ?? []) : undefined}
                        showSpy={showSpy}
                        showQqq={showQqq}
                        indicatorData={indicatorData}
                        activeIndicators={activeIndicators}
                        trades={trades}
                        emaOverlays={emaOverlays}
                        onChartReady={setMainChart}
                      />
                    ) : (
                      <div style={styles.empty}>Loading {ticker}...</div>
                    )}
                  </div>
                </div>

                {/* BOTTOM PANE: Strategy rules + Results — fixed height, scrolls */}
                <div style={{ height: 300, flexShrink: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', background: 'var(--bg-main)', borderTop: '1px solid var(--border-light)' }}>
                  <StrategyBuilder
                    ticker={ticker}
                    start={start}
                    end={end}
                    interval={interval}
                    onResult={setBacktestResult}
                    dataSource={dataSource}
                    settingsPortalId="strategy-settings-portal"
                  />
                  {backtestResult && <Results result={backtestResult} mainChart={mainChart} />}
                </div>

              </div>
            </Panel>

            <Separator className="resize-handle-v" />

            {/* RIGHT SIDEBAR: Settings portal target */}
            <Panel defaultSize="20%" minSize="12%" collapsible>
              <div style={styles.rightPanel}>
                <div id="strategy-settings-portal" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }} />
              </div>
            </Panel>

          </Group>
        </div>
        <div style={{ height: '100%', display: activeTab === 'trading' ? 'block' : 'none' }}>
          <PaperTrading />
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
