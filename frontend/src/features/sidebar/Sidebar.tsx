import { useState, useEffect, useRef } from 'react'
import { Search, X } from 'lucide-react'
import { useSearch, useProviders } from '../../shared/hooks/useOHLCV'
import type { IndicatorInstance, DataSource, DatePreset } from '../../shared/types'
import IndicatorList from './IndicatorList'

interface SidebarProps {
  ticker: string
  start: string
  end: string
  interval: string
  indicators: IndicatorInstance[]
  onIndicatorsChange: React.Dispatch<React.SetStateAction<IndicatorInstance[]>>
  showSpy: boolean
  showQqq: boolean
  onTickerChange: (t: string) => void
  onStartChange: (s: string) => void
  onEndChange: (e: string) => void
  onIntervalChange: (i: string) => void
  onToggleSpy: () => void
  onToggleQqq: () => void
  dataSource: DataSource
  onDataSourceChange: (s: DataSource) => void
  extendedHours: boolean
  onExtendedHoursChange: (v: boolean) => void
  datePreset: DatePreset
  onDatePresetChange: (preset: DatePreset) => void
  /** F222: surface backend 404 ("No data") so the clamp chip can render in error form. */
  dataError?: boolean
}

const INTERVAL_LIMITS: Record<string, number> = {
  '1m': 7,
  '5m': 60,
  '15m': 60,
  '30m': 60,
  '1h': 730,
}

/** Return the calendar-aligned period start containing `date` for a given preset.
 *  D = same day, W = Monday of that week, M = 1st of month, Q = quarter start, Y = Jan 1 */
function periodStart(date: Date, preset: DatePreset): Date {
  const y = date.getFullYear(), m = date.getMonth(), d = date.getDate()
  switch (preset) {
    case 'D': return new Date(y, m, d)
    case 'W': {
      const dow = date.getDay() // 0=Sun
      const mon = d - ((dow + 6) % 7) // shift so Mon=0
      return new Date(y, m, mon)
    }
    case 'M': return new Date(y, m, 1)
    case 'Q': return new Date(y, m - (m % 3), 1)
    case 'Y': return new Date(y, 0, 1)
    default: return new Date(y, m, d)
  }
}

/** Return the calendar-aligned period end (exclusive → last day of period) for a given preset. */
function periodEnd(startDate: Date, preset: DatePreset): Date {
  const y = startDate.getFullYear(), m = startDate.getMonth(), d = startDate.getDate()
  switch (preset) {
    case 'D': return new Date(y, m, d) // same day
    case 'W': return new Date(y, m, d + 6) // Mon–Sun
    case 'M': return new Date(y, m + 1, 0) // last day of month
    case 'Q': return new Date(y, m + 3, 0) // last day of quarter
    case 'Y': return new Date(y, 11, 31)
    default: return new Date(y, m, d)
  }
}

function fmt(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function computePresetRange(end: string, preset: DatePreset): { start: string; end: string } {
  if (preset === 'custom') return { start: end, end }
  const endDate = new Date(end + 'T00:00:00')
  const ps = periodStart(endDate, preset)
  const pe = periodEnd(ps, preset)
  return { start: fmt(ps), end: fmt(pe) }
}

function stepRange(
  start: string, _end: string, preset: DatePreset, direction: 1 | -1
): { start: string; end: string } {
  const s = new Date(start + 'T00:00:00')

  if (preset === 'custom') {
    const e = new Date(_end + 'T00:00:00')
    const days = Math.round((e.getTime() - s.getTime()) / (1000 * 60 * 60 * 24))
    s.setDate(s.getDate() + days * direction)
    e.setDate(e.getDate() + days * direction)
    return { start: fmt(s), end: fmt(e) }
  }

  // Step to the next/prev aligned period from the current period start
  let newStart: Date
  switch (preset) {
    case 'D': newStart = new Date(s.getFullYear(), s.getMonth(), s.getDate() + direction); break
    case 'W': newStart = new Date(s.getFullYear(), s.getMonth(), s.getDate() + 7 * direction); break
    case 'M': newStart = new Date(s.getFullYear(), s.getMonth() + direction, 1); break
    case 'Q': newStart = new Date(s.getFullYear(), s.getMonth() + 3 * direction, 1); break
    case 'Y': newStart = new Date(s.getFullYear() + direction, 0, 1); break
    default: newStart = s
  }

  return { start: fmt(newStart), end: fmt(periodEnd(newStart, preset)) }
}

export default function Sidebar({
  ticker, start, end, interval, indicators, onIndicatorsChange, showSpy, showQqq,
  onTickerChange, onStartChange, onEndChange, onIntervalChange,
  onToggleSpy, onToggleQqq,
  dataSource, onDataSourceChange,
  extendedHours, onExtendedHoursChange,
  datePreset, onDatePresetChange,
  dataError,
}: SidebarProps) {
  const daysDiff = Math.round(
    (new Date(end).getTime() - new Date(start).getTime()) / (1000 * 60 * 60 * 24)
  )
  const intervalLimit = INTERVAL_LIMITS[interval]
  const isIntradayClamped = dataSource === 'yahoo' && intervalLimit !== undefined && daysDiff > intervalLimit
  // F222: effective From = To minus the clamp window. Computed client-side from
  // the documented yfinance limits; if the provider ever changes its limits the
  // chip will lie until INTERVAL_LIMITS is updated (acceptable per plan v1).
  const effectiveStart = isIntradayClamped
    ? new Date(new Date(end).getTime() - (intervalLimit! - 1) * 86400_000)
        .toISOString().slice(0, 10)
    : ''
  // The chip also renders in error form when the backend returns 404 — it's the
  // only place the user learns the picked window resolved to no data.
  const showClampError = dataError && intervalLimit !== undefined

  const { data: providers = ['yahoo'] } = useProviders()

  const [indicatorsCollapsed, setIndicatorsCollapsed] = useState(false)
  const [compareCollapsed, setCompareCollapsed] = useState(false)

  const [localStart, setLocalStart] = useState(start)
  const [localEnd, setLocalEnd] = useState(end)
  useEffect(() => setLocalStart(start), [start])
  useEffect(() => setLocalEnd(end), [end])

  const handlePresetChange = (preset: DatePreset) => {
    onDatePresetChange(preset)
    if (preset !== 'custom') {
      const range = computePresetRange(end, preset)
      onStartChange(range.start)
      onEndChange(range.end)
    }
  }

  const today = new Date().toISOString().slice(0, 10)

  const handleStep = (direction: 1 | -1, multiplier: number = 1) => {
    let newStart = start
    let newEnd = end
    for (let i = 0; i < multiplier; i++) {
      const stepped = stepRange(newStart, newEnd, datePreset, direction)
      // Forward stepping stops once the next period starts past today
      if (direction === 1 && stepped.start > today) break
      newStart = stepped.start
      newEnd = stepped.end
    }
    onStartChange(newStart)
    onEndChange(newEnd)
  }

  // Disable forward arrows when a single step would land past today
  const forwardDisabled = stepRange(start, end, datePreset, 1).start > today

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
          {(['yahoo', 'alpaca', 'ibkr'] as const).map(src => {
            const available = providers.includes(src)
            const active = dataSource === src || (src === 'alpaca' && dataSource === 'alpaca-iex')
            return (
              <button
                key={src}
                onClick={() => available && onDataSourceChange(src)}
                disabled={!available}
                title={!available ? (src === 'ibkr' ? 'Set IBKR_HOST + IBKR_PORT in backend/.env and start IB Gateway' : 'Set ALPACA_API_KEY + ALPACA_SECRET_KEY in backend/.env to enable') : undefined}
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
        {(dataSource === 'yahoo' || dataSource === 'ibkr') && ['1m','5m','15m','30m','1h','60m'].includes(interval) && (
          <label style={{ ...styles.checkRow, marginTop: 12, marginBottom: 0 }}>
            <input
              type="checkbox"
              checked={extendedHours}
              onChange={() => onExtendedHoursChange(!extendedHours)}
            />
            <span style={{ marginLeft: 8, fontSize: 12 }}>Extended hours <span style={{ color: 'var(--text-muted)' }}>(pre/post)</span></span>
          </label>
        )}
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Date Range</div>

        {/* Preset selector */}
        <div style={{ display: 'flex', gap: 2, background: 'var(--bg-input)', borderRadius: 'var(--radius-sm)', padding: 2, marginBottom: 8 }}>
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
              {p === 'custom' ? '…' : p}
            </button>
          ))}
        </div>

        {/* Stepping arrows */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 2, marginBottom: 8 }}>
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
          <div style={{ flex: 1 }} />
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

        {/* From/To — always visible */}
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
          {isIntradayClamped && (
            <div style={{
              fontSize: 11, marginTop: 8, lineHeight: 1.4, padding: '8px 10px',
              border: '1px solid var(--accent-orange)', borderRadius: 4,
              background: 'rgba(255,165,0,0.08)', color: 'var(--accent-orange)',
            }}>
              <div style={{ fontWeight: 600 }}>⚠ Intraday clamped to last {intervalLimit} days</div>
              <div style={{ marginTop: 4, color: 'var(--text-muted)' }}>
                Effective range: {effectiveStart} → {end}
              </div>
              <button
                onClick={() => onStartChange(effectiveStart)}
                style={{
                  marginTop: 6, padding: '3px 8px', fontSize: 11,
                  background: 'var(--accent-orange)', color: '#000', border: 'none',
                  borderRadius: 3, cursor: 'pointer', fontWeight: 600,
                }}
              >
                Use effective range
              </button>
            </div>
          )}
          {showClampError && !isIntradayClamped && (
            <div style={{
              fontSize: 11, marginTop: 8, lineHeight: 1.4, padding: '8px 10px',
              border: '1px solid var(--accent-red, #c33)', borderRadius: 4,
              background: 'rgba(204,51,51,0.08)', color: 'var(--accent-red, #c33)',
            }}>
              <div style={{ fontWeight: 600 }}>⚠ No data for selected range</div>
              <div style={{ marginTop: 4, color: 'var(--text-muted)' }}>
                {interval} intraday data is limited to the last {intervalLimit} days.
                Your range may be entirely before the clamp window.
              </div>
            </div>
          )}
        </div>
      </div>

      <div style={styles.section}>
        <div
          onClick={() => setIndicatorsCollapsed(c => !c)}
          style={{ ...styles.sectionTitle, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, userSelect: 'none' }}
        >
          <span style={{ fontSize: 10, transform: indicatorsCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>▼</span>
          Indicators
        </div>
        {!indicatorsCollapsed && (
          <IndicatorList indicators={indicators} onChange={onIndicatorsChange} />
        )}
      </div>

      <div style={styles.section}>
        <div
          onClick={() => setCompareCollapsed(c => !c)}
          style={{ ...styles.sectionTitle, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, userSelect: 'none' }}
        >
          <span style={{ fontSize: 10, transform: compareCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>▼</span>
          Compare
        </div>
        {!compareCollapsed && (
          <>
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
          </>
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
