import { useState, useEffect, useRef } from 'react'
import { Search, X } from 'lucide-react'
import { useSearch, useProviders } from '../../shared/hooks/useOHLCV'
import type { IndicatorKey, DataSource, MAType, DatePreset } from '../../shared/types'
import type { MASettings } from '../../App'

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
  maSettings: MASettings
  onMaSettingsChange: (s: MASettings) => void
  datePreset: DatePreset
  onDatePresetChange: (preset: DatePreset) => void
}

const sgInputStyle: React.CSSProperties = {
  width: 38, fontSize: 11, padding: '2px 4px',
  background: 'var(--bg-input)', border: '1px solid var(--border-light)',
  borderRadius: 4, color: 'var(--text-primary)', textAlign: 'center',
}

const ALL_INDICATORS: { key: IndicatorKey; label: string }[] = [
  { key: 'macd', label: 'MACD' },
  { key: 'rsi', label: 'RSI' },
  { key: 'ema', label: 'EMA (20/50/200)' },
  { key: 'bb', label: 'Bollinger Bands' },
  { key: 'ma', label: 'MA (8/21)' },
  { key: 'volume', label: 'Volume' },
]

const INTERVAL_LIMITS: Record<string, number> = {
  '1m': 7,
  '5m': 60,
  '15m': 60,
  '30m': 60,
  '1h': 730,
}

function computePresetStart(end: string, preset: DatePreset): string {
  const endDate = new Date(end + 'T00:00:00')
  let startDate: Date
  switch (preset) {
    case 'D':
      startDate = new Date(endDate)
      startDate.setDate(startDate.getDate() - 1)
      break
    case 'W':
      startDate = new Date(endDate)
      startDate.setDate(startDate.getDate() - 7)
      break
    case 'M':
      startDate = new Date(endDate)
      startDate.setMonth(startDate.getMonth() - 1)
      break
    case 'Q':
      startDate = new Date(endDate)
      startDate.setMonth(startDate.getMonth() - 3)
      break
    case 'Y':
      startDate = new Date(endDate)
      startDate.setFullYear(startDate.getFullYear() - 1)
      break
    default:
      return end // custom — no computation
  }
  return startDate.toISOString().slice(0, 10)
}

function clampToLastDay(d: Date): void {
  const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()
  if (d.getDate() > lastDay) d.setDate(lastDay)
}

function stepRange(
  start: string, end: string, preset: DatePreset, direction: 1 | -1
): { start: string; end: string } {
  const s = new Date(start + 'T00:00:00')
  const e = new Date(end + 'T00:00:00')

  if (preset === 'custom' || preset === 'D') {
    // For custom, shift by the range's duration in days; for D, shift by 1 day
    const days = preset === 'D' ? 1 : Math.round((e.getTime() - s.getTime()) / (1000 * 60 * 60 * 24))
    s.setDate(s.getDate() + days * direction)
    e.setDate(e.getDate() + days * direction)
  } else if (preset === 'W') {
    s.setDate(s.getDate() + 7 * direction)
    e.setDate(e.getDate() + 7 * direction)
  } else if (preset === 'M') {
    s.setMonth(s.getMonth() + direction)
    clampToLastDay(s)
    e.setMonth(e.getMonth() + direction)
    clampToLastDay(e)
  } else if (preset === 'Q') {
    s.setMonth(s.getMonth() + 3 * direction)
    clampToLastDay(s)
    e.setMonth(e.getMonth() + 3 * direction)
    clampToLastDay(e)
  } else if (preset === 'Y') {
    s.setFullYear(s.getFullYear() + direction)
    clampToLastDay(s)
    e.setFullYear(e.getFullYear() + direction)
    clampToLastDay(e)
  }

  return {
    start: s.toISOString().slice(0, 10),
    end: e.toISOString().slice(0, 10),
  }
}

export default function Sidebar({
  ticker, start, end, interval, activeIndicators, showSpy, showQqq,
  onTickerChange, onStartChange, onEndChange, onIntervalChange,
  onToggleIndicator, onToggleSpy, onToggleQqq,
  dataSource, onDataSourceChange,
  maSettings, onMaSettingsChange,
  datePreset, onDatePresetChange,
}: SidebarProps) {
  const daysDiff = Math.round(
    (new Date(end).getTime() - new Date(start).getTime()) / (1000 * 60 * 60 * 24)
  )
  const intervalLimit = INTERVAL_LIMITS[interval]
  const showIntervalWarning = dataSource === 'yahoo' && intervalLimit !== undefined && daysDiff > intervalLimit

  const { data: providers = ['yahoo'] } = useProviders()

  const [localStart, setLocalStart] = useState(start)
  const [localEnd, setLocalEnd] = useState(end)
  useEffect(() => setLocalStart(start), [start])
  useEffect(() => setLocalEnd(end), [end])

  const handlePresetChange = (preset: DatePreset) => {
    onDatePresetChange(preset)
    if (preset !== 'custom') {
      const newStart = computePresetStart(end, preset)
      onStartChange(newStart)
    }
  }

  const handleStep = (direction: 1 | -1, multiplier: number = 1) => {
    let newStart = start
    let newEnd = end
    for (let i = 0; i < multiplier; i++) {
      const stepped = stepRange(newStart, newEnd, datePreset, direction)
      newStart = stepped.start
      newEnd = stepped.end
    }
    onStartChange(newStart)
    onEndChange(newEnd)
  }

  const today = new Date().toISOString().slice(0, 10)
  const forwardDisabled = end >= today

  const [query, setQuery] = useState('')
  const [showDropdown, setShowDropdown] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
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
            <Search size={14} color="var(--text-muted)" />
            <input
              style={styles.searchInput}
              value={showDropdown ? query : ticker}
              onChange={e => { setQuery(e.target.value); setShowDropdown(true); setSelectedIndex(0) }}
              onFocus={() => { setQuery(''); setShowDropdown(true); setSelectedIndex(0) }}
              onKeyDown={e => {
                const maxIndex = (searchResults?.length || 1) - 1;
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setSelectedIndex(i => Math.min(i + 1, maxIndex));
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setSelectedIndex(i => Math.max(i - 1, 0));
                } else if (e.key === 'Enter') {
                  const target = (searchResults && searchResults.length > selectedIndex) ? searchResults[selectedIndex].symbol : query;
                  if (target) {
                    onTickerChange(target.toUpperCase());
                    setQuery('');
                    setShowDropdown(false);
                  }
                }
              }}
              placeholder="Search ticker..."
            />
            {query && <button onClick={() => setQuery('')}><X size={12} color="var(--text-muted)" /></button>}
          </div>
          {showDropdown && searchResults && searchResults.length > 0 && (
            <div style={styles.dropdown}>
              {searchResults.map((r: any, i: number) => (
                <div key={r.symbol} style={{ ...styles.dropdownItem, backgroundColor: i === selectedIndex ? 'var(--bg-panel-hover)' : 'transparent' }}
                  onMouseEnter={() => setSelectedIndex(i)}
                  onClick={() => { onTickerChange(r.symbol); setQuery(''); setShowDropdown(false) }}>
                  <span style={{ fontWeight: 600, color: 'var(--accent-primary)' }}>{r.symbol}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div style={{ marginTop: 12, fontSize: 20, fontWeight: 700, color: 'var(--text-primary)' }}>{ticker}</div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Data Source</div>
        <div style={styles.segmentedToggle}>
          {(['yahoo', 'alpaca'] as const).map(src => {
            const available = providers.includes(src)
            const active = dataSource === src || (src === 'alpaca' && dataSource === 'alpaca-iex')
            return (
              <button
                key={src}
                onClick={() => available && onDataSourceChange(src)}
                disabled={!available}
                title={!available ? 'Set ALPACA_API_KEY in .env to enable' : undefined}
                style={{
                  flex: 1,
                  padding: '6px 0',
                  fontSize: 12,
                  fontWeight: 600,
                  background: active ? 'var(--bg-panel-hover)' : 'transparent',
                  color: active ? 'var(--text-primary)' : available ? 'var(--text-secondary)' : 'var(--text-muted)',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  cursor: available ? 'pointer' : 'not-allowed',
                  opacity: available ? 1 : 0.5,
                  transition: 'all 0.2s',
                  boxShadow: active ? 'var(--shadow-sm)' : 'none',
                }}
              >
                {src.charAt(0).toUpperCase() + src.slice(1)}
              </button>
            )
          })}
        </div>
        {(dataSource === 'alpaca' || dataSource === 'alpaca-iex') && (
          <label style={{ ...styles.checkRow, marginTop: 12, marginBottom: 0 }}>
            <input
              type="checkbox"
              checked={dataSource === 'alpaca-iex'}
              onChange={() => onDataSourceChange(dataSource === 'alpaca-iex' ? 'alpaca' : 'alpaca-iex')}
            />
            <span style={{ marginLeft: 8, fontSize: 12 }}>IEX feed <span style={{ color: 'var(--accent-green)' }}>(real-time)</span></span>
          </label>
        )}
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Date Range</div>

        {/* Preset row with arrows */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 2, marginBottom: 12 }}>
          <button
            onClick={() => handleStep(-1, 5)}
            style={styles.arrowBtn}
            title="Back 5 periods"
          >
            «
          </button>
          <button
            onClick={() => handleStep(-1)}
            style={styles.arrowBtn}
            title="Previous period"
          >
            ‹
          </button>
          <div style={{ display: 'flex', flex: 1, gap: 2, background: 'var(--bg-input)', borderRadius: 'var(--radius-sm)', padding: 2 }}>
            {(['D', 'W', 'M', 'Q', 'Y', 'custom'] as DatePreset[]).map(p => (
              <button
                key={p}
                onClick={() => handlePresetChange(p)}
                style={{
                  flex: 1,
                  padding: '5px 0',
                  fontSize: 11,
                  fontWeight: 600,
                  border: 'none',
                  borderRadius: 3,
                  cursor: 'pointer',
                  background: datePreset === p ? 'var(--bg-panel-hover)' : 'transparent',
                  color: datePreset === p ? 'var(--text-primary)' : 'var(--text-muted)',
                  transition: 'all 0.15s',
                }}
              >
                {p === 'custom' ? '⚙' : p}
              </button>
            ))}
          </div>
          <button
            onClick={() => handleStep(1)}
            disabled={forwardDisabled}
            style={{ ...styles.arrowBtn, opacity: forwardDisabled ? 0.3 : 1, cursor: forwardDisabled ? 'not-allowed' : 'pointer' }}
            title="Next period"
          >
            ›
          </button>
          <button
            onClick={() => handleStep(1, 5)}
            disabled={forwardDisabled}
            style={{ ...styles.arrowBtn, opacity: forwardDisabled ? 0.3 : 1, cursor: forwardDisabled ? 'not-allowed' : 'pointer' }}
            title="Forward 5 periods"
          >
            »
          </button>
        </div>

        {/* Custom From/To — only visible when custom preset */}
        {datePreset === 'custom' && (
          <>
            <div style={styles.field}>
              <label style={styles.label}>From</label>
              <input
                type="date" value={localStart} style={styles.dateInput}
                onChange={e => setLocalStart(e.target.value)}
                onBlur={e => onStartChange(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && onStartChange((e.target as HTMLInputElement).value)}
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>To</label>
              <input
                type="date" value={localEnd} style={styles.dateInput}
                onChange={e => setLocalEnd(e.target.value)}
                onBlur={e => onEndChange(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && onEndChange((e.target as HTMLInputElement).value)}
              />
            </div>
          </>
        )}

        {/* Interval — always visible */}
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
            <div style={{ fontSize: 11, color: 'var(--accent-orange)', marginTop: 8, lineHeight: 1.4 }}>
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
            />
            <span style={{ marginLeft: 8 }}>{label}</span>
          </label>
        ))}
        {activeIndicators.includes('ma') && (
          <div style={{ marginTop: 4, marginLeft: 24, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {/* MA type selector */}
            <div style={{ display: 'flex', gap: 2, borderRadius: 'var(--radius-sm)', background: 'var(--bg-input)', padding: 2 }}>
              {(['sma', 'ema', 'rma'] as MAType[]).map(t => (
                <button
                  key={t}
                  onClick={() => onMaSettingsChange({ ...maSettings, type: t })}
                  style={{
                    flex: 1, padding: '3px 0', fontSize: 11, fontWeight: 600, border: 'none', borderRadius: 3, cursor: 'pointer',
                    background: maSettings.type === t ? 'var(--bg-panel-hover)' : 'transparent',
                    color: maSettings.type === t ? 'var(--text-primary)' : 'var(--text-muted)',
                  }}
                >{t.toUpperCase()}</button>
              ))}
            </div>
            {/* S-G display mode toggles */}
            <label style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer' }}>
              <input type="checkbox" checked={maSettings.compensateLag} onChange={() => onMaSettingsChange({ ...maSettings, compensateLag: !maSettings.compensateLag, predictiveSg: false })} />
              compensate S-G lag
            </label>
            <label style={{ fontSize: 10, color: maSettings.predictiveSg ? 'var(--accent-orange)' : 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer' }}>
              <input type="checkbox" checked={maSettings.predictiveSg} onChange={() => onMaSettingsChange({ ...maSettings, predictiveSg: !maSettings.predictiveSg, compensateLag: false })} />
              predictive S-G
            </label>
            {/* MA8 settings */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: '#e8ab6a', minWidth: 32 }}>MA8</span>
                <label style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer' }}>
                  <input type="checkbox" checked={maSettings.showRaw8} onChange={() => onMaSettingsChange({ ...maSettings, showRaw8: !maSettings.showRaw8 })} />
                  raw
                </label>
                <label style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer' }}>
                  <input type="checkbox" checked={maSettings.showSg8} onChange={() => onMaSettingsChange({ ...maSettings, showSg8: !maSettings.showSg8 })} />
                  S-G
                </label>
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginLeft: 2 }}>
                <label style={{ fontSize: 10, color: 'var(--text-muted)' }}>win</label>
                <input type="number" min={3} step={2} value={maSettings.sg8Window}
                  onChange={e => { let v = parseInt(e.target.value); if (!isNaN(v)) { if (v < 3) v = 3; if (v % 2 === 0) v += 1; onMaSettingsChange({ ...maSettings, sg8Window: v }) } }}
                  style={sgInputStyle}
                />
                <label style={{ fontSize: 10, color: 'var(--text-muted)' }}>poly</label>
                <input type="number" min={1} max={5} value={maSettings.sg8Poly}
                  onChange={e => { const v = parseInt(e.target.value); if (!isNaN(v) && v >= 1 && v < maSettings.sg8Window) onMaSettingsChange({ ...maSettings, sg8Poly: v }) }}
                  style={sgInputStyle}
                />
              </div>
            </div>
            {/* MA21 settings */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: '#56d4c4', minWidth: 32 }}>MA21</span>
                <label style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer' }}>
                  <input type="checkbox" checked={maSettings.showRaw21} onChange={() => onMaSettingsChange({ ...maSettings, showRaw21: !maSettings.showRaw21 })} />
                  raw
                </label>
                <label style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 3, cursor: 'pointer' }}>
                  <input type="checkbox" checked={maSettings.showSg21} onChange={() => onMaSettingsChange({ ...maSettings, showSg21: !maSettings.showSg21 })} />
                  S-G
                </label>
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginLeft: 2 }}>
                <label style={{ fontSize: 10, color: 'var(--text-muted)' }}>win</label>
                <input type="number" min={3} step={2} value={maSettings.sg21Window}
                  onChange={e => { let v = parseInt(e.target.value); if (!isNaN(v)) { if (v < 3) v = 3; if (v % 2 === 0) v += 1; onMaSettingsChange({ ...maSettings, sg21Window: v }) } }}
                  style={sgInputStyle}
                />
                <label style={{ fontSize: 10, color: 'var(--text-muted)' }}>poly</label>
                <input type="number" min={1} max={5} value={maSettings.sg21Poly}
                  onChange={e => { const v = parseInt(e.target.value); if (!isNaN(v) && v >= 1 && v < maSettings.sg21Window) onMaSettingsChange({ ...maSettings, sg21Poly: v }) }}
                  style={sgInputStyle}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Compare</div>
        <label style={styles.checkRow}>
          <input type="checkbox" checked={showSpy} onChange={onToggleSpy} style={{ accentColor: 'var(--accent-orange)' }} />
          <span style={{ marginLeft: 8, color: 'var(--accent-orange)' }}>SPY</span>
        </label>
        <label style={styles.checkRow}>
          <input type="checkbox" checked={showQqq} onChange={onToggleQqq} style={{ accentColor: 'var(--accent-purple)' }} />
          <span style={{ marginLeft: 8, color: 'var(--accent-purple)' }}>QQQ</span>
        </label>
        {(showSpy || showQqq) && (
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>% change from start</div>
        )}
      </div>
    </aside>
  )
}

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    height: '100%',
    background: 'var(--bg-main)',
    borderRight: '1px solid var(--border-light)',
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 0,
  },
  section: { padding: '16px 20px', borderBottom: '1px solid var(--border-light)' },
  sectionTitle: { fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 16 },
  searchBox: { display: 'flex', alignItems: 'center', gap: 8, background: 'var(--bg-input)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', padding: '8px 12px', transition: 'border-color 0.2s' },
  searchInput: { background: 'none', border: 'none', outline: 'none', color: 'var(--text-primary)', flex: 1, fontSize: 13, padding: 0 },
  dropdown: { position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, background: 'var(--bg-panel)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)', zIndex: 100, maxHeight: 220, overflowY: 'auto', boxShadow: 'var(--shadow-md)' },
  dropdownItem: { padding: '10px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', fontSize: 13, borderBottom: '1px solid var(--border-light)' },
  segmentedToggle: { display: 'flex', borderRadius: 'var(--radius-md)', background: 'var(--bg-input)', padding: 4, gap: 2 },
  field: { marginBottom: 12 },
  label: { display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6, fontWeight: 500 },
  dateInput: { width: '100%', fontSize: 13, padding: '8px 12px' },
  checkRow: { display: 'flex', alignItems: 'center', marginBottom: 10, cursor: 'pointer', fontSize: 13, color: 'var(--text-primary)' },
  arrowBtn: {
    width: 28, height: 28,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 14, fontWeight: 700,
    background: 'var(--bg-input)', border: '1px solid var(--border-light)',
    borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
    cursor: 'pointer', flexShrink: 0,
  },
}
