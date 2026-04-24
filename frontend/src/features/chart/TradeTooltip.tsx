import type { Trade } from '../../shared/types'

interface TradeTooltipProps {
  x: number
  y: number
  trades: Trade[]
  allTrades: Trade[]
  candleTimeIndex: Map<string | number, number>
  toET: (ts: any) => number
}

function holdBars(
  exitTrade: Trade,
  allTrades: Trade[],
  candleTimeIndex: Map<string | number, number>,
  toET: (ts: any) => number,
): number | null {
  if (exitTrade.type === 'buy' || exitTrade.type === 'short') return null
  const exitIdx = allTrades.indexOf(exitTrade)
  if (exitIdx < 1) return null
  const entryType = exitTrade.type === 'sell' ? 'buy' : 'short'
  let entryTrade: Trade | null = null
  for (let i = exitIdx - 1; i >= 0; i--) {
    if (allTrades[i].type === entryType) {
      entryTrade = allTrades[i]
      break
    }
  }
  if (!entryTrade) return null
  const ei = candleTimeIndex.get(toET(entryTrade.date as any))
  const xi = candleTimeIndex.get(toET(exitTrade.date as any))
  if (ei === undefined || xi === undefined) return null
  return xi - ei
}

function exitReason(t: Trade): string {
  if (t.stop_loss) return 'SL'
  if (t.trailing_stop) return 'TSL'
  return 'Signal'
}

const label: React.CSSProperties = { color: '#8b949e', fontSize: 10 }
const row: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', gap: 8 }

export default function TradeTooltip({ x, y, trades, allTrades, candleTimeIndex, toET }: TradeTooltipProps) {
  const showAbove = y > 200

  const style: React.CSSProperties = {
    position: 'absolute',
    left: Math.max(0, Math.min(x - 100, 9999)),
    pointerEvents: 'none',
    zIndex: 10,
    background: '#1c2128',
    border: '1px solid #30363d',
    borderRadius: 6,
    padding: '8px 10px',
    fontSize: 11,
    color: '#e6edf3',
    minWidth: 170,
    maxWidth: 260,
    whiteSpace: 'nowrap',
    ...(showAbove ? { bottom: `calc(100% - ${y}px + 12px)` } : { top: y + 12 }),
  }

  return (
    <div style={style}>
      {trades.map((t, i) => {
        const isEntry = t.type === 'buy' || t.type === 'short'
        const win = (t.pnl ?? 0) >= 0
        const pnlColor = win ? '#26a641' : '#f85149'

        return (
          <div key={i}>
            {i > 0 && <div style={{ borderTop: '1px solid #30363d', margin: '5px 0' }} />}
            <div style={{ ...row, marginBottom: 3 }}>
              <span style={{ fontWeight: 600, color: isEntry ? '#e5c07b' : pnlColor }}>
                {t.type === 'buy' ? 'BUY' : t.type === 'short' ? 'SHORT' : t.type === 'sell' ? 'SELL' : 'COVER'}
              </span>
              <span>${t.price.toFixed(2)}</span>
            </div>

            {isEntry ? (
              <>
                <div style={row}>
                  <span style={label}>Shares</span>
                  <span>{t.shares.toFixed(1)}</span>
                </div>
                {(t.slippage ?? 0) > 0 && (
                  <div style={row}>
                    <span style={label}>Slippage</span>
                    <span>${t.slippage!.toFixed(2)}</span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div style={row}>
                  <span style={label}>P&L</span>
                  <span style={{ color: pnlColor }}>
                    {win ? '+' : ''}{t.pnl?.toFixed(2)} ({win ? '+' : ''}{t.pnl_pct?.toFixed(2)}%)
                  </span>
                </div>
                {(() => {
                  const bars = holdBars(t, allTrades, candleTimeIndex, toET)
                  return bars !== null ? (
                    <div style={row}>
                      <span style={label}>Held</span>
                      <span>{bars} bars</span>
                    </div>
                  ) : null
                })()}
                <div style={row}>
                  <span style={label}>Exit</span>
                  <span>{exitReason(t)}</span>
                </div>
                {((t.slippage ?? 0) > 0 || (t.commission ?? 0) > 0 || (t.borrow_cost ?? 0) > 0) && (
                  <div style={{ ...row, color: '#f0883e', fontSize: 10, marginTop: 2 }}>
                    {(t.slippage ?? 0) > 0 && <span>Slip ${t.slippage!.toFixed(2)}</span>}
                    {(t.commission ?? 0) > 0 && <span>Comm ${t.commission!.toFixed(2)}</span>}
                    {(t.borrow_cost ?? 0) > 0 && <span>Borr ${t.borrow_cost!.toFixed(2)}</span>}
                  </div>
                )}
              </>
            )}
            {t.rules && t.rules.length > 0 && (
              <div style={{ marginTop: 3, borderTop: '1px solid #30363d', paddingTop: 3 }}>
                <div style={{ ...label, marginBottom: 2 }}>Rules</div>
                {t.rules.map((r, ri) => (
                  <div key={ri} style={{ fontSize: 10, color: '#8b949e', whiteSpace: 'normal' }}>{r}</div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
