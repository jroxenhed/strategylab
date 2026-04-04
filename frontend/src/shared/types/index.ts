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

export interface MACDData {
  macd: TimeValue[]
  signal: TimeValue[]
  histogram: TimeValue[]
}

export interface BBData {
  upper: TimeValue[]
  middle: TimeValue[]
  lower: TimeValue[]
}

export interface EMAData {
  ema20: TimeValue[]
  ema50: TimeValue[]
  ema200: TimeValue[]
}

export interface IndicatorData {
  macd?: MACDData
  rsi?: TimeValue[]
  ema?: EMAData
  bb?: BBData
  volume?: TimeValue[]
}

export type IndicatorKey = 'macd' | 'rsi' | 'ema' | 'bb' | 'volume'

export interface Rule {
  indicator: 'macd' | 'rsi' | 'price' | 'ema20' | 'ema50' | 'ema200'
  condition: 'crossover_up' | 'crossover_down' | 'above' | 'below' | 'crosses_above' | 'crosses_below'
  value?: number
  param?: string
}

export interface StrategyRequest {
  ticker: string
  start: string
  end: string
  interval: string
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
  initial_capital: number
  position_size: number
}

export interface Trade {
  type: 'buy' | 'sell'
  date: string
  price: number
  shares: number
  pnl?: number
  pnl_pct?: number
}

export interface BacktestResult {
  summary: {
    initial_capital: number
    final_value: number
    total_return_pct: number
    buy_hold_return_pct: number
    num_trades: number
    win_rate_pct: number
    sharpe_ratio: number
    max_drawdown_pct: number
  }
  trades: Trade[]
  equity_curve: TimeValue[]
}

export interface AppState {
  ticker: string
  start: string
  end: string
  interval: string
  activeIndicators: IndicatorKey[]
  showSpy: boolean
  showQqq: boolean
}

export type DataSource = 'yahoo' | 'alpaca'
