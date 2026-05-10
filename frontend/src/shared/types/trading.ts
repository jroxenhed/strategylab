import type { Rule, TrailingStopConfig, DynamicSizingConfig, SkipAfterStopConfig, TradingHoursConfig, RegimeConfig, BacktestResult } from './strategy'

export interface BotConfig {
  bot_id: string
  strategy_name: string
  symbol: string
  interval: string
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
  long_buy_rules?: Rule[]
  long_sell_rules?: Rule[]
  long_buy_logic?: 'AND' | 'OR'
  long_sell_logic?: 'AND' | 'OR'
  short_buy_rules?: Rule[]
  short_sell_rules?: Rule[]
  short_buy_logic?: 'AND' | 'OR'
  short_sell_logic?: 'AND' | 'OR'
  allocated_capital: number
  position_size: number
  stop_loss_pct?: number
  max_bars_held?: number
  trailing_stop?: TrailingStopConfig
  dynamic_sizing?: DynamicSizingConfig
  skip_after_stop?: SkipAfterStopConfig
  trading_hours?: TradingHoursConfig
  slippage_bps?: number
  max_spread_bps?: number | null
  drawdown_threshold_pct?: number | null
  data_source?: string
  direction?: 'long' | 'short'
  broker?: string
  regime?: RegimeConfig
}

export interface BotFundStatus {
  bot_fund: number
  allocated: number
  available: number
}

export interface BotActivityEntry {
  time: string
  msg: string
  level: 'INFO' | 'WARN' | 'ERROR' | 'TRADE'
}

export interface BotState {
  status: 'stopped' | 'backtesting' | 'running' | 'error'
  started_at?: string
  last_scan_at?: string
  last_tick?: string
  last_signal?: string
  last_price?: number
  trades_count: number
  equity_snapshots: { time: string; value: number }[]
  backtest_summary?: BacktestResult['summary']
  activity_log: BotActivityEntry[]
  error_message?: string
  pause_reason?: string
}

export interface BotSummary {
  bot_id: string
  strategy_name: string
  symbol: string
  interval: string
  allocated_capital: number
  status: string
  trades_count: number
  total_pnl: number
  backtest_summary: Record<string, number> | null
  data_source?: string
  avg_cost_bps?: number | null
  has_position?: boolean
  direction?: 'long' | 'short'
  broker?: string
  max_spread_bps?: number | null
  drawdown_threshold_pct?: number | null
  first_trade_time?: string | null
  last_tick?: string
  pause_reason?: string
  equity_snapshots?: { time: string; value: number }[]
  regime_direction?: string | null
  position_direction?: string | null
  pending_regime_flip?: boolean
  was_running?: boolean
}

export interface BotDetail {
  config: BotConfig
  state?: BotState
}

export interface BotListResponse {
  fund: BotFundStatus
  bots: BotSummary[]
}
