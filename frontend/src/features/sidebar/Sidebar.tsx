import { useState, useEffect, useRef } from 'react'
import { Search, X } from 'lucide-react'
import { useSearch, useProviders } from '../../shared/hooks/useOHLCV'
import type { IndicatorKey, DataSource } from '../../shared/types'

interface SidebarProps {
  ticker: string
  start: string
  end: string
  interval: string
  activeIndicators: IndicatorKey[]
  showSpy: boolean
  showQqq: boolean
  onTickerChange: (t: string) => void
  onStartChange: (s: string) => void
  onEndChange: (e: string) => void
  onIntervalChange: (i: string) => void
  onToggleIndicator: (k: IndicatorKey) => void
  onToggleSpy: () => void
  onToggleQqq: () => void
  dataSource: DataSource
  onDataSourceChange: (s: DataSource) => void
}

const ALL_INDICATORS: { key: IndicatorKey; label: string }[] = [
  { key: 'macd', label: 'MACD' },
  { key: 'rsi', label: 'RSI' },
  { key: 'ema', label: 'EMA (20/50/200)' },
  { key: 'bb', label: 'Bollinger Bands' },
  { key: 'volume', label: 'Volume' },
]

const INTERVAL_LIMITS: Record<string, number> = {
  '1m': 7,
  '5m': 60,
  '15m': 60,
  '30m': 60,
  '1h': 730,
}

export default function Sidebar({
  ticker, start, end, interval, activeIndicators, showSpy, showQqq,
  onTickerChange, onStartChange, onEndChange, onIntervalChange,
  onToggleIndicator, onToggleSpy, onToggleQqq,
  dataSource, onDataSourceChange,
}: SidebarProps) {
  const daysDiff = Math.round(
    (new Date(end).getTime() - new Date(start).getTime()) / (1000 * 60 * 60 * 24)
  )
  const intervalLimit = INTERVAL_LIMITS[interval]
  const showIntervalWarning = dataSource === 'yahoo' && intervalLimit !== undefined && daysDiff > intervalLimit

  const { data: providers = ['yahoo'] } = useProviders()

  const [query, setQuery] = useState('')
  const [showDropdown, setShowDropdown] = useState(false)
  const { data: searchResults } = useSearch(query)
  const dropRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handle(e: MouseEvent) {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setShowDropdown(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [])

  return (
    <aside style={styles.sidebar}>
      <div style={styles.section}>
        <div style={styles.sectionTitle}>Ticker</div>
        <div style={{ position: 'relative' }} ref={dropRef}>
          <div style={styles.searchBox}>
            <Search size={14} color="#8b949e" />
            <input
              style={styles.searchInput}
              value={query || ticker}
              onChange={e => { setQuery(e.target.value); setShowDropdown(true) }}
              onFocus={() => { setQuery(''); setShowDropdown(true) }}
              placeholder="Search ticker..."
            />
            {query && <button onClick={() => setQuery('')}><X size={12} color="#8b949e" /></button>}
          </div>
          {showDropdown && searchResults && searchResults.length > 0 && (
            <div style={styles.dropdown}>
              {searchResults.map((r: any) => (
                <div key={r.symbol} style={styles.dropdownItem}
                  onClick={() => { onTickerChange(r.symbol); setQuery(''); setShowDropdown(false) }}>
                  <span style={{ fontWeight: 600, color: '#58a6ff' }}>{r.symbol}</span>
                  <span style={{ fontSize: 11, color: '#8b949e', marginLeft: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div style={{ marginTop: 8, fontSize: 18, fontWeight: 700, color: '#58a6ff' }}>{ticker}</div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Data Source</div>
        <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', border: '1px solid #30363d' }}>
          {(['yahoo', 'alpaca'] as const).map(src => {
            const available = providers.includes(src)
            const active = dataSource === src
            return (
              <button
                key={src}
                onClick={() => available && onDataSourceChange(src)}
                disabled={!available}
                title={!available ? 'Set ALPACA_API_KEY in .env to enable' : undefined}
                style={{
                  flex: 1,
                  padding: '5px 0',
                  fontSize: 12,
                  fontWeight: 600,
                  background: active ? '#58a6ff' : '#0d1117',
                  color: active ? '#000' : available ? '#e6edf3' : '#484f58',
                  border: 'none',
                  cursor: available ? 'pointer' : 'not-allowed',
                  opacity: available ? 1 : 0.5,
                }}
              >
                {src.charAt(0).toUpperCase() + src.slice(1)}
              </button>
            )
          })}
        </div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Date Range</div>
        <div style={styles.field}>
          <label style={styles.label}>From</label>
          <input type="date" value={start} onChange={e => onStartChange(e.target.value)} style={styles.dateInput} />
        </div>
        <div style={styles.field}>
          <label style={styles.label}>To</label>
          <input type="date" value={end} onChange={e => onEndChange(e.target.value)} style={styles.dateInput} />
        </div>
        <div style={styles.field}>
          <label style={styles.label}>Interval</label>
          <select value={interval} onChange={e => onIntervalChange(e.target.value)} style={styles.dateInput}>
            <option value="1m">1 min</option>
            <option value="5m">5 min</option>
            <option value="15m">15 min</option>
            <option value="30m">30 min</option>
            <option value="1h">1 Hour</option>
            <option value="1d">Daily</option>
            <option value="1wk">Weekly</option>
            <option value="1mo">Monthly</option>
          </select>
          {showIntervalWarning && (
            <div style={{ fontSize: 11, color: '#f0883e', marginTop: 6, lineHeight: 1.4 }}>
              {interval} data only supports {intervalLimit} days of history. Your range is {daysDiff} days — shorten the From date.
            </div>
          )}
        </div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Indicators</div>
        {ALL_INDICATORS.map(({ key, label }) => (
          <label key={key} style={styles.checkRow}>
            <input
              type="checkbox"
              checked={activeIndicators.includes(key)}
              onChange={() => onToggleIndicator(key)}
              style={{ accentColor: '#58a6ff' }}
            />
            <span style={{ marginLeft: 8 }}>{label}</span>
          </label>
        ))}
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Compare</div>
        <label style={styles.checkRow}>
          <input type="checkbox" checked={showSpy} onChange={onToggleSpy} style={{ accentColor: '#f0883e' }} />
          <span style={{ marginLeft: 8, color: '#f0883e' }}>SPY</span>
        </label>
        <label style={styles.checkRow}>
          <input type="checkbox" checked={showQqq} onChange={onToggleQqq} style={{ accentColor: '#a371f7' }} />
          <span style={{ marginLeft: 8, color: '#a371f7' }}>QQQ</span>
        </label>
        {(showSpy || showQqq) && (
          <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>% change from start (left axis)</div>
        )}
      </div>
    </aside>
  )
}

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    width: 220,
    minWidth: 220,
    background: '#161b22',
    borderRight: '1px solid #30363d',
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 0,
  },
  section: { padding: '14px 12px', borderBottom: '1px solid #21262d' },
  sectionTitle: { fontSize: 11, fontWeight: 600, color: '#8b949e', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 },
  searchBox: { display: 'flex', alignItems: 'center', gap: 6, background: '#0d1117', border: '1px solid #30363d', borderRadius: 6, padding: '5px 8px' },
  searchInput: { background: 'none', border: 'none', outline: 'none', color: '#e6edf3', flex: 1, fontSize: 13, padding: 0 },
  dropdown: { position: 'absolute', top: '100%', left: 0, right: 0, background: '#161b22', border: '1px solid #30363d', borderRadius: 6, zIndex: 100, maxHeight: 220, overflowY: 'auto' },
  dropdownItem: { padding: '7px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', fontSize: 13 },
  field: { marginBottom: 8 },
  label: { display: 'block', fontSize: 11, color: '#8b949e', marginBottom: 3 },
  dateInput: { width: '100%', fontSize: 12 },
  checkRow: { display: 'flex', alignItems: 'center', marginBottom: 8, cursor: 'pointer', fontSize: 13 },
}
