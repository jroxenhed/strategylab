import type { IndicatorType, IndicatorInstance, IndicatorTypeDef, ParamField, ParamFieldNumber, ParamFieldSelect } from './indicators'
export type { IndicatorType, IndicatorInstance, IndicatorTypeDef, ParamField, ParamFieldNumber, ParamFieldSelect }
export { INDICATOR_DEFS, DEFAULT_INDICATORS, createInstance, paramSummary } from './indicators'

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

export interface Rule {
  indicator: 'macd' | 'rsi' | 'price' | 'ma' | 'bb' | 'atr' | 'atr_pct' | 'volume' | 'stochastic' | 'adx'
  condition: string
  value?: number
  param?: string
  threshold?: number
  muted?: boolean
  negated?: boolean
  visualize?: boolean
  params?: Record<string, any>
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
  consec_sls: number      // consecutive qualifying stops before reducing size
  reduced_pct: number     // position size % to use when triggered
  trigger?: 'sl' | 'tsl' | 'both'  // default 'sl'
}

export interface SkipAfterStopConfig {
  enabled: boolean
  count: number
  trigger: 'sl' | 'tsl' | 'both'
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
  max_bars_held?: number
  trailing_stop?: TrailingStopConfig
  dynamic_sizing?: DynamicSizingConfig
  skip_after_stop?: SkipAfterStopConfig
  trading_hours?: TradingHoursConfig
  slippage_bps?: number
  commission_pct?: number
  per_share_rate?: number
  min_per_order?: number
  borrow_rate_annual?: number
  source: DataSource
  debug?: boolean
  direction?: 'long' | 'short'
  extended_hours?: boolean
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
  skipAfterStop?: SkipAfterStopConfig
  tradingHours: TradingHoursConfig
  slippageBps: number | ''
  commission: number | ''
  perShareRate?: number
  minPerOrder?: number
  borrowRateAnnual?: number
  maxBarsHeld?: number | ''
  direction: 'long' | 'short'
  indicators?: IndicatorInstance[]
  pinned?: boolean
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
  borrow_cost?: number
  direction?: 'long' | 'short'
  rules?: string[]
}

export interface EMAOverlay {
  indicator: string
  condition: string
  lookback: number
  series: TimeValue[]
  active: boolean[]
  side: 'buy' | 'sell'
}

export interface RuleSignal {
  rule_index: number
  label: string
  side: 'buy' | 'sell'
  signals: Array<{ time: number | string; price: number }>
}

export interface SessionAnalyticsBucket {
  bucket: string
  trade_count: number
  wins: number
  losses: number
  win_rate: number
  avg_pnl: number
  total_pnl: number
  avg_pnl_pct: number
}

export interface MonteCarloResult {
  num_simulations: number
  num_trades: number
  curves: {
    p5: number[]
    p25: number[]
    p50: number[]
    p75: number[]
    p95: number[]
  }
  min_equity: { p5: number; p25: number; p50: number; p75: number; p95: number }
  max_drawdown_pct: { p5: number; p25: number; p50: number; p75: number; p95: number }
  ruin_probability: number
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
  rule_signals?: RuleSignal[]
  session_analytics?: SessionAnalyticsBucket[] | null
}

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
  indicators: IndicatorInstance[]
  showSpy: boolean
  showQqq: boolean
}

export type DataSource = 'yahoo' | 'alpaca' | 'alpaca-iex' | 'ibkr'

export type DatePreset = 'D' | 'W' | 'M' | 'Q' | 'Y' | 'custom'

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
}

export interface BotDetail {
  config: BotConfig
  state: BotState
}

export interface BotListResponse {
  fund: BotFundStatus
  bots: BotSummary[]
}
