import type { IndicatorInstance } from './indicators'

export interface OHLCVBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface TimeValue {
  time: string
  value: number | null
}

export type MAType = 'sma' | 'ema' | 'rma'

export interface MacroCurvePoint {
  time: string
  open: number
  high: number
  low: number
  close: number
  drawdown_pct: number
  trades: { pnl: number }[]
}

export interface PeriodStats {
  label: string
  winning_pct: number
  avg_return_pct: number
  best_return_pct: number
  worst_return_pct: number
  avg_trades: number
}

export interface MacroResponse {
  macro_curve: MacroCurvePoint[]
  bucket: string
  period_stats: PeriodStats
}

export interface AppState {
  ticker: string
  start: string
  end: string
  interval: string
  indicators: IndicatorInstance[]
  showSpy: boolean
  showQqq: boolean
}

export type DatePreset = 'D' | 'W' | 'M' | 'Q' | 'Y' | 'custom'
