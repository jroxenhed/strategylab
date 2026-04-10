import type { BotActivityEntry } from '../types'

export const COLORS = {
  profit: '#26a69a',
  loss: '#ef5350',
  warn: '#f0b429',
  accent: '#58a6ff',
  muted: '#555',
  text: '#aaa',
} as const

export function statusColor(status: string): string {
  if (status === 'running') return COLORS.profit
  if (status === 'error') return COLORS.loss
  if (status === 'backtesting') return COLORS.warn
  return COLORS.muted
}

export function levelColor(level: BotActivityEntry['level']): string {
  if (level === 'TRADE') return COLORS.profit
  if (level === 'ERROR') return COLORS.loss
  if (level === 'WARN') return COLORS.warn
  return COLORS.text
}

export function pnlColor(value: number): string {
  return value >= 0 ? COLORS.profit : COLORS.loss
}
