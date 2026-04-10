export function fmtUsd(n: number): string {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

export function fmtPnl(n: number): string {
  const s = Math.abs(n).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
  return n >= 0 ? `+${s}` : `-${s}`
}

export function fmtPct(n: number, decimals = 2): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`
}
