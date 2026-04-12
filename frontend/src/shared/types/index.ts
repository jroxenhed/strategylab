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

export type MAType = 'sma' | 'ema' | 'rma'

export interface MAData {
  ma8: TimeValue[]
  ma21: TimeValue[]
  ma8_sg: TimeValue[]
  ma21_sg: TimeValue[]
  ma_type: MAType
  sg8_window: number
  sg8_poly: number
  sg21_window: number
  sg21_poly: number
}

export interface IndicatorData {
  macd?: MACDData
  rsi?: TimeValue[]
  ema?: EMAData
  bb?: BBData
  ma?: MAData
  volume?: TimeValue[]
}

export type IndicatorKey = 'macd' | 'rsi' | 'ema' | 'bb' | 'ma' | 'volume'

export interface Rule {
  indicator: 'macd' | 'rsi' | 'price' | 'ema20' | 'ema50' | 'ema200' | 'ma8' | 'ma21'
  condition: 'crossover_up' | 'crossover_down' | 'above' | 'below' | 'crosses_above' | 'crosses_below' | 'turns_up_below' | 'turns_down_above' | 'rising' | 'falling' | 'rising_over' | 'falling_over' | 'turns_up' | 'turns_down' | 'decelerating' | 'accelerating'
  value?: number
  param?: string
  muted?: boolean
  negated?: boolean
}

export interface TrailingStopConfig {
  type: 'pct' | 'atr'
  value: number
  source: 'high' | 'close'
  activate_on_profit: boolean
  activate_pct: number  // min profit % before trailing starts (0 = any profit)
}

export interface DynamicSizingConfig {
  enabled: boolean
  consec_sls: number      // consecutive stop losses before reducing size
  reduced_pct: number     // position size % to use when triggered
}

export interface TradingHoursConfig {
  enabled: boolean
  start_time: string      // ET time e.g. "09:30"
  end_time: string        // ET time e.g. "16:00"
  skip_ranges: string[]   // ET time ranges to skip, e.g. ["12:00-13:00", "15:45-16:00"]
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
  stop_loss_pct?: number
  trailing_stop?: TrailingStopConfig
  dynamic_sizing?: DynamicSizingConfig
  trading_hours?: TradingHoursConfig
  slippage_pct?: number
  commission_pct?: number
  source: DataSource
  debug?: boolean
  direction?: 'long' | 'short'
  ma_type?: string
  sg8_window?: number
  sg8_poly?: number
  sg21_window?: number
  sg21_poly?: number
}

export interface SavedStrategy {
  name: string
  savedAt: string              // ISO date string
  ticker?: string
  interval?: string
  buyRules: Rule[]
  sellRules: Rule[]
  buyLogic: 'AND' | 'OR'
  sellLogic: 'AND' | 'OR'
  capital: number
  posSize: number
  stopLoss: number | ''
  trailingEnabled: boolean
  trailingConfig: TrailingStopConfig
  dynamicSizing: DynamicSizingConfig
  tradingHours: TradingHoursConfig
  slippage: number | ''
  commission: number | ''
  direction: 'long' | 'short'
}

export interface Trade {
  type: 'buy' | 'sell' | 'short' | 'cover'
  date: string | number
  price: number
  shares: number
  pnl?: number
  pnl_pct?: number
  stop_loss?: boolean
  trailing_stop?: boolean
  slippage?: number
  commission?: number
  direction?: 'long' | 'short'
}

export interface EMAOverlay {
  indicator: string
  condition: string
  lookback: number
  series: TimeValue[]
  active: boolean[]
  side: 'buy' | 'sell'
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
    gain_stats?: SideStats
    loss_stats?: SideStats
    pnl_distribution?: number[]
    gross_profit?: number
    gross_loss?: number
    ev_per_trade?: number | null
    profit_factor?: number | null
  }
  trades: Trade[]
  equity_curve: TimeValue[]
  baseline_curve?: TimeValue[]
  ema_overlays?: EMAOverlay[]
  signal_trace?: SignalTraceEntry[]
}

export interface SideStats {
  min: number | null
  max: number | null
  mean: number | null
  median: number | null
}

export interface SignalTraceRule {
  rule: string
  result: boolean
  muted?: boolean
  v_now?: number | null
  v_prev?: number | null
}

export interface SignalTraceEntry {
  date: string | number
  price: number
  position: string
  action: string
  buy_rules?: SignalTraceRule[]
  sell_rules?: SignalTraceRule[]
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

export type DataSource = 'yahoo' | 'alpaca' | 'alpaca-iex'

// ---------------------------------------------------------------------------
// Bot types
// ---------------------------------------------------------------------------

export interface BotConfig {
  bot_id: string
  strategy_name: string
  symbol: string
  interval: string
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
  allocated_capital: number
  position_size: number
  stop_loss_pct?: number
  trailing_stop?: TrailingStopConfig
  dynamic_sizing?: DynamicSizingConfig
  trading_hours?: TradingHoursConfig
  slippage_pct?: number
  data_source?: string
  direction?: 'long' | 'short'
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
  last_signal?: string
  last_price?: number
  trades_count: number
  total_pnl: number
  equity_snapshots: { time: string; value: number }[]
  backtest_result?: {
    summary: BacktestResult['summary']
    equity_curve: { time: string; value: number }[]
  }
  activity_log: BotActivityEntry[]
  error_message?: string
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
  avg_slippage_pct?: number | null
  has_position?: boolean
  direction?: 'long' | 'short'
  first_trade_time?: string | null
}

export interface BotDetail {
  config: BotConfig
  state: BotState
}

export interface BotListResponse {
  fund: BotFundStatus
  bots: BotSummary[]
}
