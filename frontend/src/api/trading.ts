import { api } from './client'
import type { Rule } from '../shared/types'
import type { WatchlistGroup } from '../features/watchlist/watchlistStorage'

// --- Types ---

export interface Account {
  equity: number
  cash: number
  buying_power: number
  portfolio_value: number
  day_trade_count: number
  pattern_day_trader: boolean
  trading_blocked: boolean
  account_blocked: boolean
}

export interface Position {
  symbol: string
  qty: number
  side: string
  avg_entry: number
  current_price: number
  market_value: number
  unrealized_pl: number
  unrealized_pl_pct: number
  broker: string
}

export interface Order {
  id: string
  symbol: string
  side: string
  qty: string
  type: string
  status: string
  filled_avg_price: string | null
  submitted_at: string
  filled_at: string | null
  broker: string
}

export interface SignalResult {
  symbol: string
  signal: 'BUY' | 'SELL' | 'NONE' | 'ERROR'
  price?: number
  rsi?: number
  ema50?: number
  last_bar?: string
  error?: string
}

export interface ScanAction {
  symbol: string
  action: string
  qty?: number
  order_id?: string
  stop_price?: number
  detail?: string
}

export interface ScanResponse {
  signals: SignalResult[]
  scanned_at: string
  actions?: ScanAction[]
}

export interface ScanRequest {
  symbols: string[]
  interval: string
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
  auto_execute?: boolean
  position_size_usd?: number
  stop_loss_pct?: number
}

export interface JournalTrade {
  id: string
  timestamp: string
  symbol: string
  side: string
  qty: number
  price: number | null
  stop_loss_price: number | null
  source: string
  reason: string | null
  expected_price: number | null
  broker: string | null
  slippage_bps: number | null
  bot_id: string | null
  direction: string | null
  borrow_cost: number | null
}

export interface JournalResponse {
  trades: JournalTrade[]
  total: number
}

export interface StaleAware<T> {
  rows: T[]
  stale_brokers: string[]
}

export interface PerformanceRequest {
  symbol: string
  start: string
  end?: string
  interval: string
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
}

export interface PerformanceEquityPoint {
  time: string
  value: number
}

export interface PerformanceResponse {
  symbol: string
  period: { start: string; end: string }
  actual: {
    trade_count: number
    completed_trades: number
    total_pnl: number
    win_rate_pct: number
    equity_curve: PerformanceEquityPoint[]
  }
  backtest: {
    trade_count: number
    total_return_pct: number
    win_rate_pct: number
    sharpe_ratio: number
    equity_curve: PerformanceEquityPoint[]
  } | null
}

// --- API calls ---

export async function fetchAccount(signal?: AbortSignal): Promise<Account> {
  const { data } = await api.get('/api/trading/account', { signal })
  return data
}

export async function fetchPositions(broker: string = 'all', signal?: AbortSignal): Promise<StaleAware<Position>> {
  const { data } = await api.get('/api/trading/positions', { params: { broker }, signal })
  return { rows: data.positions ?? [], stale_brokers: data.stale_brokers ?? [] }
}

export async function fetchOrders(broker: string = 'all', signal?: AbortSignal): Promise<StaleAware<Order>> {
  const { data } = await api.get('/api/trading/orders', { params: { broker }, signal })
  return { rows: data.orders ?? [], stale_brokers: data.stale_brokers ?? [] }
}

export async function placeBuy(symbol: string, qty: number, stop_loss_pct?: number) {
  const { data } = await api.post('/api/trading/buy', { symbol, qty, stop_loss_pct })
  return data
}

export async function placeSell(symbol: string, qty?: number, broker?: string) {
  const { data } = await api.post('/api/trading/sell', { symbol, qty, broker })
  return data
}

export async function closeAll() {
  const { data } = await api.post('/api/trading/close-all')
  return data
}

export async function cancelAll() {
  const { data } = await api.post('/api/trading/cancel-all')
  return data
}

export async function scanSignals(req: ScanRequest): Promise<ScanResponse> {
  const { data } = await api.post('/api/trading/scan', req)
  return data
}

export async function fetchWatchlist(): Promise<string[]> {
  const { data } = await api.get('/api/trading/watchlist')
  // Backend returns { groups: WatchlistGroup[], ungrouped: string[] }.
  // Flatten all tickers preserving order, dedup (first occurrence wins).
  const groups: Array<{ tickers?: string[] }> = data.groups ?? []
  const ungrouped: string[] = data.ungrouped ?? []
  const all = [...groups.flatMap(g => g.tickers ?? []), ...ungrouped]
  const seen = new Set<string>()
  const result: string[] = []
  for (const t of all) {
    const key = t.toUpperCase()
    if (!seen.has(key)) { seen.add(key); result.push(t) }
  }
  return result
}

/**
 * saveWatchlist — read-modify-write: preserves existing groups, updates only
 * the ungrouped list (scanner-owned symbols minus any already in a group).
 *
 * Any symbol in `symbols` that already belongs to a group is left there and
 * excluded from ungrouped to avoid duplication. Two saves in a row are
 * idempotent.
 *
 * NOTE: concurrent calls share a TOCTOU race window (GET→POST with no
 * server-side CAS). Two simultaneous scanner saves can overwrite each other;
 * the window is narrow in practice (scanner saves are user-triggered) but
 * is not eliminated. See K-02 for background.
 *
 * A failed GET now throws (aborts the save) rather than proceeding with empty
 * groups — proceeding would silently wipe the user's group membership.
 */
export async function saveWatchlist(symbols: string[]): Promise<void> {
  // Fetch current state to preserve groups.
  // If the GET fails, abort — proceeding with empty groups would overwrite
  // all group membership with an ungrouped list, which is a silent data wipe.
  let currentGroups: WatchlistGroup[] = []
  const { data } = await api.get('/api/trading/watchlist')
  currentGroups = data.groups ?? []

  // Build set of all tickers already in groups
  const inGroup = new Set<string>(
    currentGroups.flatMap(g => (g.tickers ?? []).map(t => t.toUpperCase()))
  )

  // ungrouped = requested symbols NOT already in a group (deduped, order-preserving)
  const seen = new Set<string>()
  const ungrouped: string[] = []
  for (const t of symbols) {
    const key = t.toUpperCase()
    if (!inGroup.has(key) && !seen.has(key)) {
      seen.add(key)
      ungrouped.push(t)
    }
  }

  await api.post('/api/trading/watchlist', { groups: currentGroups, ungrouped })
}

export async function fetchJournal(symbol?: string, broker: string = 'all', signal?: AbortSignal, limit?: number): Promise<JournalResponse> {
  const params: Record<string, string | number> = { broker }
  if (symbol) params.symbol = symbol
  if (limit != null) params.limit = limit
  const { data } = await api.get('/api/trading/journal', { params, signal })
  return { trades: data.trades ?? [], total: data.total ?? 0 }
}

export async function fetchPerformance(req: PerformanceRequest): Promise<PerformanceResponse> {
  const { data } = await api.post('/api/trading/performance', req)
  return data
}

// --- Broker ---

export interface BrokerHealth {
  healthy: boolean
  last_ok_ts: number | null
  last_error: string | null
}

export interface BrokerInfo {
  active: string
  available: string[]
  health: Record<string, BrokerHealth>
  heartbeat_warmup: boolean
  poll_interval_ms: number | null
  api_calls_per_minute: number
  data_calls_per_minute: number
}

export async function fetchBroker(): Promise<BrokerInfo> {
  const { data } = await api.get('/api/broker')
  return data
}

export async function setBroker(broker: string): Promise<BrokerInfo> {
  const { data } = await api.put('/api/broker', { broker })
  return data
}

// --- Quick batch backtest ---

export interface QuickBacktestResult {
  ticker: string
  return_pct: number | null
  sharpe: number | null
  win_rate_pct: number | null
  num_trades: number | null
  max_drawdown_pct: number | null
  signal_now: boolean | null
  last_signal_date: string | null
  error: string | null
}

export interface BatchQuickBacktestRequest {
  symbols: string[]
  interval: string
  lookback_days: number
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
  direction?: 'long' | 'short'
}

export async function batchQuickBacktest(req: BatchQuickBacktestRequest): Promise<QuickBacktestResult[]> {
  const { data } = await api.post('/api/backtest/quick/batch', req)
  return data.results ?? []
}
