import { api } from './client'
import type { Rule } from '../shared/types'

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

export async function fetchAccount(): Promise<Account> {
  const { data } = await api.get('/api/trading/account')
  return data
}

export async function fetchPositions(): Promise<Position[]> {
  const { data } = await api.get('/api/trading/positions')
  return data
}

export async function fetchOrders(): Promise<Order[]> {
  const { data } = await api.get('/api/trading/orders')
  return data
}

export async function placeBuy(symbol: string, qty: number, stop_loss_pct?: number) {
  const { data } = await api.post('/api/trading/buy', { symbol, qty, stop_loss_pct })
  return data
}

export async function placeSell(symbol: string, qty?: number) {
  const { data } = await api.post('/api/trading/sell', { symbol, qty })
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
  return data.symbols ?? []
}

export async function saveWatchlist(symbols: string[]): Promise<void> {
  await api.post('/api/trading/watchlist', { symbols })
}

export async function fetchJournal(symbol?: string): Promise<JournalTrade[]> {
  const params = symbol ? { symbol } : {}
  const { data } = await api.get('/api/trading/journal', { params })
  return data.trades ?? []
}

export async function fetchPerformance(req: PerformanceRequest): Promise<PerformanceResponse> {
  const { data } = await api.post('/api/trading/performance', req)
  return data
}

// --- Broker ---

export interface BrokerInfo {
  active: string
  available: string[]
}

export async function fetchBroker(): Promise<BrokerInfo> {
  const { data } = await api.get('/api/broker')
  return data
}

export async function setBroker(broker: string): Promise<BrokerInfo> {
  const { data } = await api.put('/api/broker', { broker })
  return data
}
