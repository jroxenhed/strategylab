export type IndicatorType = 'rsi' | 'macd' | 'bb' | 'atr' | 'ma' | 'volume' | 'stochastic' | 'vwap' | 'adx'

export type IndicatorInstance = {
  id: string
  type: IndicatorType
  params: Record<string, number | string>
  enabled: boolean
  color?: string
  pane: 'main' | 'sub'
}

export type ParamFieldNumber = { key: string; label: string; kind: 'number'; min?: number; max?: number }
export type ParamFieldSelect = { key: string; label: string; kind: 'select'; options: { value: string; label: string }[] }
export type ParamField = ParamFieldNumber | ParamFieldSelect

export type IndicatorTypeDef = {
  type: IndicatorType
  label: string
  defaultParams: Record<string, number | string>
  pane: 'main' | 'sub'
  paramFields: ParamField[]
  subPaneSharing?: 'shared' | 'isolated'
}

export function generateInstanceId(type: IndicatorType): string {
  return `${type}-${crypto.randomUUID().slice(0, 8)}`
}

export function createInstance(type: IndicatorType, overrides?: Partial<IndicatorInstance>): IndicatorInstance {
  const def = INDICATOR_DEFS[type]
  return {
    id: generateInstanceId(type),
    type,
    params: { ...def.defaultParams },
    enabled: true,
    pane: def.pane,
    ...overrides,
  }
}

export const INDICATOR_DEFS: Record<IndicatorType, IndicatorTypeDef> = {
  rsi: {
    type: 'rsi', label: 'RSI',
    defaultParams: { period: 14, type: 'wilder' },
    pane: 'sub',
    paramFields: [
      { key: 'period', label: 'Period', kind: 'number', min: 2 },
      { key: 'type', label: 'Type', kind: 'select', options: [{ value: 'sma', label: 'SMA' }, { value: 'wilder', label: 'Wilder' }] },
    ],
    subPaneSharing: 'shared',
  },
  macd: {
    type: 'macd', label: 'MACD',
    defaultParams: { fast: 12, slow: 26, signal: 9 },
    pane: 'sub',
    paramFields: [
      { key: 'fast', label: 'Fast', kind: 'number', min: 2 },
      { key: 'slow', label: 'Slow', kind: 'number', min: 2 },
      { key: 'signal', label: 'Signal', kind: 'number', min: 2 },
    ],
    subPaneSharing: 'isolated',
  },
  bb: {
    type: 'bb', label: 'Bollinger Bands',
    defaultParams: { period: 20, stddev: 2 },
    pane: 'main',
    paramFields: [
      { key: 'period', label: 'Period', kind: 'number', min: 2 },
      { key: 'stddev', label: 'Std Dev', kind: 'number', min: 0.5, max: 5 },
    ],
  },
  atr: {
    type: 'atr', label: 'ATR',
    defaultParams: { period: 14 },
    pane: 'sub',
    paramFields: [{ key: 'period', label: 'Period', kind: 'number', min: 2 }],
    subPaneSharing: 'shared',
  },
  ma: {
    type: 'ma', label: 'MA',
    defaultParams: { period: 20, type: 'ema' },
    pane: 'main',
    paramFields: [
      { key: 'period', label: 'Period', kind: 'number', min: 2 },
      { key: 'type', label: 'Type', kind: 'select', options: [
        { value: 'sma', label: 'SMA' },
        { value: 'ema', label: 'EMA' },
        { value: 'rma', label: 'RMA' },
      ]},
    ],
  },
  volume: {
    type: 'volume', label: 'Volume',
    defaultParams: { coloring: 'candle' },
    pane: 'main',
    paramFields: [
      { key: 'coloring', label: 'Color', kind: 'select', options: [
        { value: 'normal', label: 'Normal' },
        { value: 'candle', label: 'By candle' },
      ]},
    ],
  },
  stochastic: {
    type: 'stochastic', label: 'Stochastic',
    defaultParams: { k_period: 14, d_period: 3, smooth_k: 3 },
    pane: 'sub',
    paramFields: [
      { key: 'k_period', label: 'K Period', kind: 'number', min: 2 },
      { key: 'd_period', label: 'D Period', kind: 'number', min: 2 },
      { key: 'smooth_k', label: 'Smooth K', kind: 'number', min: 1 },
    ],
    subPaneSharing: 'shared',
  },
  vwap: {
    type: 'vwap', label: 'VWAP',
    defaultParams: {},
    pane: 'main',
    paramFields: [],
  },
  adx: {
    type: 'adx', label: 'ADX',
    defaultParams: { period: 14 },
    pane: 'sub',
    paramFields: [{ key: 'period', label: 'Period', kind: 'number', min: 2 }],
    subPaneSharing: 'shared',
  },
}

export function paramSummary(inst: IndicatorInstance): string {
  const def = INDICATOR_DEFS[inst.type]
  if (def.paramFields.length === 0) return ''
  return def.paramFields.filter(f => f.kind === 'number').map(f => inst.params[f.key]).join(',')
}

export const DEFAULT_INDICATORS: IndicatorInstance[] = [
  { id: 'macd-1', type: 'macd', params: { fast: 12, slow: 26, signal: 9 }, enabled: true, pane: 'sub' },
  { id: 'rsi-1', type: 'rsi', params: { period: 14, type: 'wilder' }, enabled: true, pane: 'sub' },
]
