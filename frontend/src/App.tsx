import { useState, useCallback } from 'react'
import type { BacktestResult, IndicatorKey } from './types'
import { useOHLCV, useIndicators } from './hooks/useOHLCV'
import Sidebar from './components/Sidebar'
import Chart from './components/Chart'
import StrategyBuilder from './components/StrategyBuilder'
import Results from './components/Results'

const today = new Date().toISOString().slice(0, 10)
const oneYearAgo = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)

export default function App() {
  const [ticker, setTicker] = useState('AAPL')
  const [start, setStart] = useState(oneYearAgo)
  const [end, setEnd] = useState(today)
  const [interval, setInterval] = useState('1d')
  const [activeIndicators, setActiveIndicators] = useState<IndicatorKey[]>(['macd', 'rsi'])
  const [showSpy, setShowSpy] = useState(false)
  const [showQqq, setShowQqq] = useState(false)
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null)

  const { data: ohlcv = [] } = useOHLCV(ticker, start, end, interval)
  const { data: spyData } = useOHLCV('SPY', start, end, interval)
  const { data: qqqData } = useOHLCV('QQQ', start, end, interval)

  const indicatorKeys = activeIndicators.filter(k => k !== 'volume')
  const { data: indicatorData = {} } = useIndicators(ticker, start, end, interval, indicatorKeys)

  const toggleIndicator = useCallback((key: IndicatorKey) => {
    setActiveIndicators(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    )
  }, [])

  const trades = backtestResult?.trades ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Header */}
      <header style={styles.header}>
        <span style={styles.logo}>StrategyLab</span>
        <span style={{ color: '#8b949e', fontSize: 13 }}>
          {ticker} &nbsp;·&nbsp; {start} → {end}
        </span>
      </header>

      {/* Main area */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar
          ticker={ticker}
          start={start}
          end={end}
          interval={interval}
          activeIndicators={activeIndicators}
          showSpy={showSpy}
          showQqq={showQqq}
          onTickerChange={t => { setTicker(t); setBacktestResult(null) }}
          onStartChange={setStart}
          onEndChange={setEnd}
          onIntervalChange={setInterval}
          onToggleIndicator={toggleIndicator}
          onToggleSpy={() => setShowSpy(v => !v)}
          onToggleQqq={() => setShowQqq(v => !v)}
        />

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Chart */}
          <div style={{ flex: 1, overflow: 'hidden' }}>
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
              />
            ) : (
              <div style={styles.empty}>Loading {ticker}…</div>
            )}
          </div>

          {/* Strategy builder */}
          <StrategyBuilder
            ticker={ticker}
            start={start}
            end={end}
            interval={interval}
            onResult={setBacktestResult}
          />

          {/* Results */}
          {backtestResult && <Results result={backtestResult} />}
        </div>
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
  empty: { display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#8b949e', fontSize: 14 },
}
