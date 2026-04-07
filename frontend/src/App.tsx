import { useState, useCallback, useEffect } from 'react'
import type { BacktestResult, IndicatorKey, DataSource } from './shared/types'
import { useOHLCV, useIndicators } from './shared/hooks/useOHLCV'
import Sidebar from './features/sidebar/Sidebar'
import Chart from './features/chart/Chart'
import StrategyBuilder from './features/strategy/StrategyBuilder'
import Results from './features/strategy/Results'
import PaperTrading from './features/trading/PaperTrading'

type AppTab = 'chart' | 'trading'

const STORAGE_KEY = 'strategylab-settings'
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
  const [showChart, setShowChart] = useState(true)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      ticker, start, end, interval, activeIndicators, showSpy, showQqq, dataSource,
    }))
  }, [ticker, start, end, interval, activeIndicators, showSpy, showQqq, dataSource])

  const { data: ohlcv = [] } = useOHLCV(ticker, start, end, interval, dataSource)
  const { data: spyData } = useOHLCV('SPY', start, end, interval, dataSource)
  const { data: qqqData } = useOHLCV('QQQ', start, end, interval, dataSource)

  const indicatorKeys = activeIndicators.filter(k => k !== 'volume')
  const { data: indicatorData = {} } = useIndicators(ticker, start, end, interval, indicatorKeys, dataSource)

  const toggleIndicator = useCallback((key: IndicatorKey) => {
    setActiveIndicators(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    )
  }, [])

  const trades = backtestResult?.trades ?? []
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
              style={{
                ...styles.tab,
                ...(activeTab === tab ? styles.tabActive : {}),
              }}
            >
              {tab === 'chart' ? 'Chart' : 'Paper Trading'}
            </button>
          ))}
        </div>
        <span style={{ color: '#8b949e', fontSize: 13 }}>
          {ticker} &nbsp;·&nbsp; {start} → {end}
        </span>
      </header>

      {/* Main area */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {activeTab === 'chart' ? (
          <>
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

            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              {/* Chart toggle bar */}
              <div style={styles.chartToggleBar}>
                <button onClick={() => setShowChart(v => !v)} style={styles.chartToggleBtn}>
                  {showChart ? '▾ Hide Chart' : '▸ Show Chart'}
                </button>
              </div>

              {/* Chart */}
              <div style={{ flex: showChart ? 1 : 0, overflow: 'hidden', display: showChart ? 'block' : 'none' }}>
                {ohlcv.length > 0 ? (
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
                  />
                ) : (
                  <div style={styles.empty}>Loading {ticker}...</div>
                )}
              </div>

              {/* Strategy builder */}
              <StrategyBuilder
                ticker={ticker}
                start={start}
                end={end}
                interval={interval}
                onResult={setBacktestResult}
                dataSource={dataSource}
              />

              {/* Results */}
              {backtestResult && <Results result={backtestResult} />}
            </div>
          </>
        ) : (
          <PaperTrading />
        )}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: 'flex', alignItems: 'center', gap: 16,
    padding: '0 16px', height: 44,
    background: '#161b22', borderBottom: '1px solid #30363d',
    flexShrink: 0,
  },
  logo: { fontWeight: 700, fontSize: 16, color: '#58a6ff', letterSpacing: '-0.02em' },
  tabs: { display: 'flex', gap: 4 },
  tab: {
    fontSize: 12, padding: '4px 12px', borderRadius: 6,
    background: 'transparent', color: '#8b949e', border: '1px solid transparent',
    cursor: 'pointer', fontWeight: 500,
  },
  tabActive: {
    background: '#21262d', color: '#e6edf3', border: '1px solid #30363d',
  },
  empty: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#8b949e', fontSize: 14 },
  chartToggleBar: {
    display: 'flex', alignItems: 'center',
    padding: '2px 8px', background: '#161b22', borderBottom: '1px solid #21262d',
    flexShrink: 0,
  },
  chartToggleBtn: {
    fontSize: 11, color: '#8b949e', background: 'none', border: 'none',
    cursor: 'pointer', padding: '2px 4px', fontWeight: 500,
  },
}
