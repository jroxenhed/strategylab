import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api/client'

const STORAGE_KEY = 'watchlist-symbols'
const POLL_INTERVAL = 30_000 // 30 seconds

interface Quote {
  symbol: string
  price: number | null
  change_pct: number | null
  error?: string
}

function loadSymbols(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

function saveSymbols(symbols: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(symbols))
}

export default function WatchlistPanel({
  currentSymbol,
  onSymbolClick,
}: {
  currentSymbol: string
  onSymbolClick: (symbol: string) => void
}) {
  const [symbols, setSymbols] = useState<string[]>(loadSymbols)
  const [quotes, setQuotes] = useState<Map<string, Quote>>(new Map())
  const [hoveredSymbol, setHoveredSymbol] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Persist symbols to localStorage whenever they change
  useEffect(() => { saveSymbols(symbols) }, [symbols])

  // Fetch quotes for all watchlist symbols
  const fetchQuotes = useCallback(async () => {
    if (symbols.length === 0) return
    try {
      const { data } = await api.post('/api/quotes', symbols)
      const map = new Map<string, Quote>()
      for (const q of data as Quote[]) {
        map.set(q.symbol, q)
      }
      setQuotes(map)
    } catch {
      // silently ignore — prices will just be stale
    }
  }, [symbols])

  // Poll quotes on mount + interval
  useEffect(() => {
    fetchQuotes()
    timerRef.current = setInterval(fetchQuotes, POLL_INTERVAL)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [fetchQuotes])

  const addCurrent = useCallback(() => {
    const sym = currentSymbol.toUpperCase()
    if (!symbols.includes(sym)) {
      setSymbols(prev => [...prev, sym])
    }
  }, [currentSymbol, symbols])

  const removeSymbol = useCallback((sym: string) => {
    setSymbols(prev => prev.filter(s => s !== sym))
  }, [])

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <span style={styles.title}>Watchlist</span>
        <button
          onClick={addCurrent}
          style={styles.addBtn}
          title={`Add ${currentSymbol.toUpperCase()}`}
        >
          +
        </button>
      </div>

      {/* Symbol rows */}
      <div style={styles.list}>
        {symbols.length === 0 && (
          <div style={styles.emptyMsg}>
            Click + to add {currentSymbol}
          </div>
        )}
        {symbols.map(sym => {
          const q = quotes.get(sym)
          const isActive = sym === currentSymbol.toUpperCase()
          const isHovered = hoveredSymbol === sym
          const changePct = q?.change_pct ?? 0
          const changeColor = changePct > 0 ? 'var(--accent-green)' : changePct < 0 ? 'var(--accent-red)' : 'var(--text-muted)'
          const changePrefix = changePct > 0 ? '+' : ''

          return (
            <div
              key={sym}
              style={{
                ...styles.row,
                background: isActive
                  ? 'var(--bg-panel-hover)'
                  : isHovered
                    ? 'var(--bg-input)'
                    : 'transparent',
              }}
              onClick={() => onSymbolClick(sym)}
              onMouseEnter={() => setHoveredSymbol(sym)}
              onMouseLeave={() => setHoveredSymbol(null)}
            >
              <span style={{ ...styles.symbol, color: isActive ? 'var(--accent-primary)' : 'var(--text-primary)' }}>
                {sym}
              </span>
              <span style={styles.priceBlock}>
                {q?.price != null ? (
                  <>
                    <span style={styles.price}>{q.price.toFixed(2)}</span>
                    <span style={{ ...styles.change, color: changeColor }}>
                      {changePrefix}{changePct.toFixed(2)}%
                    </span>
                  </>
                ) : q?.error ? (
                  <span style={styles.error} title={q.error}>!</span>
                ) : (
                  <span style={styles.loading}>...</span>
                )}
              </span>
              {isHovered && (
                <button
                  style={styles.removeBtn}
                  onClick={e => { e.stopPropagation(); removeSymbol(sym) }}
                  title={`Remove ${sym}`}
                >
                  ×
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 10px',
    borderBottom: '1px solid var(--border-light)',
    flexShrink: 0,
  },
  title: {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--text-secondary)',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
  },
  addBtn: {
    background: 'none',
    border: '1px solid var(--border-light)',
    color: 'var(--text-secondary)',
    borderRadius: 4,
    width: 22,
    height: 22,
    fontSize: 15,
    lineHeight: '20px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
  },
  list: {
    flex: 1,
    overflowY: 'auto' as const,
    overflowX: 'hidden' as const,
  },
  emptyMsg: {
    padding: '16px 10px',
    fontSize: 12,
    color: 'var(--text-muted)',
    textAlign: 'center' as const,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    padding: '4px 10px',
    cursor: 'pointer',
    height: 30,
    position: 'relative' as const,
    transition: 'background 0.1s',
  },
  symbol: {
    fontWeight: 700,
    fontSize: 12,
    minWidth: 48,
  },
  priceBlock: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'flex-end',
    flex: 1,
    marginRight: 4,
  },
  price: {
    fontSize: 12,
    color: 'var(--text-primary)',
    fontVariantNumeric: 'tabular-nums',
  },
  change: {
    fontSize: 11,
    fontVariantNumeric: 'tabular-nums',
  },
  loading: {
    fontSize: 11,
    color: 'var(--text-muted)',
  },
  error: {
    fontSize: 11,
    color: 'var(--accent-red)',
    fontWeight: 700,
  },
  removeBtn: {
    position: 'absolute' as const,
    right: 4,
    top: '50%',
    transform: 'translateY(-50%)',
    background: 'var(--bg-input)',
    border: '1px solid var(--border-light)',
    color: 'var(--text-muted)',
    borderRadius: 3,
    width: 18,
    height: 18,
    fontSize: 13,
    lineHeight: '16px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
  },
}
