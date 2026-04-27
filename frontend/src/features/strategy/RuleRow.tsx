import { Trash2, VolumeX, Volume2 } from 'lucide-react'
import type { Rule } from '../../shared/types'

export const INDICATORS = ['macd', 'rsi', 'price', 'ma', 'bb', 'atr', 'atr_pct', 'volume'] as const

export const CONDITIONS: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down', 'crosses_above', 'crosses_below', 'above', 'below'],
  rsi: ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above'],
  price: ['above', 'below', 'crosses_above', 'crosses_below', 'rising', 'falling'],
  ma: ['turns_up', 'turns_down', 'decelerating', 'accelerating', 'rising', 'falling',
       'above', 'below', 'crosses_above', 'crosses_below', 'rising_over', 'falling_over'],
  bb: ['above', 'below', 'crosses_above', 'crosses_below'],
  atr: ['above', 'below', 'crosses_above', 'crosses_below', 'rising', 'falling'],
  atr_pct: ['above', 'below', 'crosses_above', 'crosses_below', 'rising', 'falling'],
  volume: ['above', 'below', 'crosses_above', 'crosses_below', 'rising', 'falling'],
}

export const CONDITION_LABELS: Record<string, string> = {
  crossover_up: 'Crosses above signal',
  crossover_down: 'Crosses below signal',
  crosses_above: 'Crosses above',
  crosses_below: 'Crosses below',
  above: 'Is above',
  below: 'Is below',
  turns_up_below: 'Turns up from below',
  turns_down_above: 'Turns down from above',
  rising: 'Is rising',
  falling: 'Is falling',
  rising_over: 'Rising over N bars',
  falling_over: 'Falling over N bars',
  turns_up: 'Turns up',
  turns_down: 'Turns down',
  decelerating: 'Decelerating',
  accelerating: 'Accelerating',
}

export const NEEDS_VALUE = ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above', 'rising_over', 'falling_over']
export const OPTIONAL_VALUE = ['turns_up', 'turns_down']

export const NEEDS_PARAM: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down'],
}

export const CAN_USE_PARAM: Record<string, string[]> = {
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ma: ['above', 'below', 'crosses_above', 'crosses_below'],
  volume: ['above', 'below', 'crosses_above', 'crosses_below'],
}

export const emptyRule = (): Rule => ({ indicator: 'macd', condition: 'crossover_up', param: 'signal' })

const BB_PARAM_LABELS: Record<string, string> = {
  upper: 'Upper',
  lower: 'Lower',
  middle: 'Middle',
  bandwidth: 'Width',
  pctb: '%B',
}

const INDICATOR_DISPLAY: Record<string, string> = {
  macd: 'MACD',
  rsi: 'RSI',
  price: 'PRICE',
  ma: 'MA',
  bb: 'BB',
  atr: 'ATR',
  atr_pct: 'ATR%',
  volume: 'VOL',
}

function indicatorLabel(rule: Rule): string {
  if (rule.indicator === 'ma' && rule.params) {
    return `MA ${rule.params.period} ${(rule.params.type || 'ema').toUpperCase()}`
  }
  if (rule.indicator === 'rsi' && rule.params) {
    const t = rule.params.type === 'wilder' ? ' Wilder' : ''
    return `RSI ${rule.params.period ?? 14}${t}`
  }
  if (rule.indicator === 'bb') {
    const p = rule.params?.period ?? 20
    const s = rule.params?.std ?? 2
    const band = BB_PARAM_LABELS[rule.param || 'upper'] || rule.param
    return `BB ${band} (${p},${s})`
  }
  if (rule.indicator === 'atr' || rule.indicator === 'atr_pct') {
    const p = rule.params?.period ?? 14
    return `${rule.indicator === 'atr_pct' ? 'ATR%' : 'ATR'} (${p})`
  }
  if (rule.indicator === 'volume') {
    const param = rule.param || 'raw'
    if (param === 'sma') {
      const p = rule.params?.period ?? 20
      return `Vol SMA(${p})`
    }
    return 'Volume'
  }
  return INDICATOR_DISPLAY[rule.indicator] || rule.indicator.toUpperCase()
}

function isRefParam(param?: string): boolean {
  if (!param) return false
  // These are cross-reference params that substitute for a numeric value
  return param === 'signal' || param === 'close' || param.startsWith('ma:') ||
    param.startsWith('bb:') || param.startsWith('atr:') || param.startsWith('volume_sma:')
}

export function validateRules(rules: Rule[], label: string): string | null {
  for (const rule of rules.filter(r => !r.muted)) {
    const hasRefParam = isRefParam(rule.param) || NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
    const needsValue = NEEDS_VALUE.includes(rule.condition) && !hasRefParam
    if (needsValue && (typeof rule.value !== 'number' || isNaN(rule.value))) {
      return `${label} rule "${indicatorLabel(rule)} ${CONDITION_LABELS[rule.condition]}" is missing a value`
    }
  }
  return null
}

type ParamMode = 'value' | 'close' | 'ma' | 'volume_sma' | 'bb'

function decodeParam(param?: string, _indicator?: string): { mode: ParamMode; period?: number; type?: string; std?: number; band?: string } {
  if (!param) return { mode: 'value' }
  if (param === 'close') return { mode: 'close' }
  if (param.startsWith('ma:')) {
    const parts = param.split(':')
    if (parts.length === 3) {
      const period = parseInt(parts[1])
      return { mode: 'ma', period: isNaN(period) ? 50 : period, type: parts[2] || 'ema' }
    }
  }
  if (param.startsWith('bb:')) {
    const parts = param.split(':')
    if (parts.length >= 4) {
      const period = parseInt(parts[1])
      const std = parseFloat(parts[2])
      return { mode: 'bb', period: isNaN(period) ? 20 : period, std: isNaN(std) ? 2 : std, band: parts[3] || 'upper' }
    }
  }
  if (param.startsWith('volume_sma:')) {
    const parts = param.split(':')
    const period = parts.length >= 2 ? parseInt(parts[1]) : 20
    return { mode: 'volume_sma', period: isNaN(period) ? 20 : period }
  }
  return { mode: 'value' }
}

function encodeParam(mode: string, period?: number, type?: string, extra?: { std?: number; band?: string }): string | undefined {
  if (mode === 'close') return 'close'
  if (mode === 'ma') return `ma:${period ?? 50}:${type ?? 'ema'}`
  if (mode === 'bb') return `bb:${period ?? 20}:${extra?.std ?? 2}:${extra?.band ?? 'upper'}`
  if (mode === 'volume_sma') return `volume_sma:${period ?? 20}`
  return undefined
}

export default function RuleRow({ rule, onChange, onDelete }: { rule: Rule; onChange: (r: Rule) => void; onDelete: () => void }) {
  const muted = rule.muted ?? false
  const conditions = CONDITIONS[rule.indicator] || []
  const canParam = CAN_USE_PARAM[rule.indicator]?.includes(rule.condition)
  const hasParam = canParam && !!rule.param
  const forcedParam = NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
  const needsValue = NEEDS_VALUE.includes(rule.condition) && !forcedParam && !hasParam
  const optionalValue = OPTIONAL_VALUE.includes(rule.condition)
  const negated = rule.negated ?? false

  return (
    <div style={{ ...styles.ruleRow, opacity: muted ? 0.4 : 1 }}>
      <button onClick={() => onChange({ ...rule, muted: !muted })} title={muted ? 'Unmute rule' : 'Mute rule'} style={{ color: muted ? '#f85149' : '#8b949e', padding: '4px 6px' }}>
        {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
      </button>
      <button
        onClick={() => onChange({ ...rule, negated: !negated })}
        title={negated ? 'Remove NOT' : 'Negate this rule (NOT)'}
        style={{
          fontSize: 10, fontWeight: 700, padding: '2px 5px', borderRadius: 4,
          border: `1px solid ${negated ? '#f0883e' : '#30363d'}`,
          background: negated ? '#f0883e22' : 'transparent',
          color: negated ? '#f0883e' : '#484f58',
          cursor: 'pointer', lineHeight: 1,
        }}
      >NOT</button>
      <select
        value={rule.indicator}
        onChange={e => {
          const newInd = e.target.value as Rule['indicator']
          const firstCond = CONDITIONS[newInd][0]
          const autoParam = NEEDS_PARAM[newInd]?.includes(firstCond) ? 'signal' : undefined
          const update: Partial<Rule> = { indicator: newInd, condition: firstCond, value: undefined, param: autoParam }
          if (newInd === 'ma') {
            update.params = { period: 20, type: 'ema' }
          } else if (newInd === 'rsi') {
            update.params = { period: 14, type: 'wilder' }
          } else if (newInd === 'bb') {
            update.params = { period: 20, std: 2 }
            update.param = 'upper'
          } else if (newInd === 'atr' || newInd === 'atr_pct') {
            update.params = { period: 14 }
          } else if (newInd === 'volume') {
            update.param = 'raw'
            update.params = { period: 20 }
          } else {
            update.params = undefined
          }
          onChange({ ...rule, ...update })
        }}
        style={styles.ruleSelect}
      >
        {INDICATORS.map(i => <option key={i} value={i}>{INDICATOR_DISPLAY[i] || i.toUpperCase()}</option>)}
      </select>
      {rule.indicator === 'ma' && (
        <>
          <input
            type="number"
            min={2}
            max={500}
            value={rule.params?.period ?? 20}
            onChange={e => onChange({ ...rule, params: { ...rule.params, period: parseInt(e.target.value) || 20, type: rule.params?.type ?? 'ema' } })}
            style={{ ...styles.ruleSelect, width: 45 }}
          />
          <select
            value={rule.params?.type ?? 'ema'}
            onChange={e => onChange({ ...rule, params: { ...rule.params, period: rule.params?.period ?? 20, type: e.target.value } })}
            style={{ ...styles.ruleSelect, width: 55 }}
          >
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
          </select>
        </>
      )}
      {rule.indicator === 'rsi' && (
        <>
          <input
            type="number"
            min={2}
            max={500}
            value={rule.params?.period ?? 14}
            onChange={e => onChange({ ...rule, params: { ...rule.params, period: parseInt(e.target.value) || 14, type: rule.params?.type ?? 'sma' } })}
            style={{ ...styles.ruleSelect, width: 45 }}
          />
          <select
            value={rule.params?.type ?? 'sma'}
            onChange={e => onChange({ ...rule, params: { ...rule.params, period: rule.params?.period ?? 14, type: e.target.value } })}
            style={{ ...styles.ruleSelect, width: 65 }}
          >
            <option value="sma">SMA</option>
            <option value="wilder">Wilder</option>
          </select>
        </>
      )}
      {rule.indicator === 'bb' && (
        <>
          <select
            value={rule.param || 'upper'}
            onChange={e => onChange({ ...rule, param: e.target.value })}
            style={styles.ruleSelect}
          >
            <option value="upper">Upper</option>
            <option value="lower">Lower</option>
            <option value="middle">Middle</option>
            <option value="bandwidth">Width</option>
            <option value="pctb">%B</option>
          </select>
          <input
            type="number"
            min={2}
            max={500}
            value={rule.params?.period ?? 20}
            onChange={e => onChange({ ...rule, params: { ...rule.params, period: parseInt(e.target.value) || 20, std: rule.params?.std ?? 2 } })}
            style={{ ...styles.ruleSelect, width: 45 }}
          />
          <input
            type="number"
            min={0.5}
            max={5}
            step={0.5}
            value={rule.params?.std ?? 2}
            onChange={e => onChange({ ...rule, params: { ...rule.params, period: rule.params?.period ?? 20, std: parseFloat(e.target.value) || 2 } })}
            style={{ ...styles.ruleSelect, width: 40 }}
            title="Std deviations"
          />
        </>
      )}
      {(rule.indicator === 'atr' || rule.indicator === 'atr_pct') && (
        <input
          type="number"
          min={2}
          max={500}
          value={rule.params?.period ?? 14}
          onChange={e => onChange({ ...rule, params: { period: parseInt(e.target.value) || 14 } })}
          style={{ ...styles.ruleSelect, width: 45 }}
        />
      )}
      {rule.indicator === 'volume' && !canParam && (
        <select
          value={rule.param || 'raw'}
          onChange={e => onChange({ ...rule, param: e.target.value })}
          style={styles.ruleSelect}
        >
          <option value="raw">Raw</option>
          <option value="sma">SMA</option>
        </select>
      )}
      {rule.indicator === 'volume' && !canParam && rule.param === 'sma' && (
        <input
          type="number"
          min={2}
          max={500}
          value={rule.params?.period ?? 20}
          onChange={e => onChange({ ...rule, params: { ...rule.params, period: parseInt(e.target.value) || 20 } })}
          style={{ ...styles.ruleSelect, width: 45 }}
        />
      )}
      <select value={rule.condition} onChange={e => {
        const newCond = e.target.value
        const keepParam = CAN_USE_PARAM[rule.indicator]?.includes(newCond)
        const forceParam = NEEDS_PARAM[rule.indicator]?.includes(newCond) ? 'signal' : undefined
        onChange({ ...rule, condition: newCond, param: forceParam ?? (keepParam ? rule.param : undefined) })
      }} style={styles.ruleSelect}>
        {conditions.map(c => <option key={c} value={c}>{CONDITION_LABELS[c] || c}</option>)}
      </select>
      {canParam && (() => {
        const decoded = decodeParam(rule.param, rule.indicator)
        const isVol = rule.indicator === 'volume'
        return (
          <>
            <select
              value={decoded.mode}
              onChange={e => {
                const m = e.target.value
                onChange({ ...rule, param: encodeParam(m, decoded.period, decoded.type, { std: decoded.std, band: decoded.band }), value: m === 'value' ? rule.value : undefined })
              }}
              style={styles.ruleSelect}
            >
              <option value="value">Value</option>
              {!isVol && <option value="close">Price</option>}
              {!isVol && <option value="ma">MA</option>}
              {!isVol && <option value="bb">BB Band</option>}
              {isVol && <option value="volume_sma">Vol SMA</option>}
            </select>
            {decoded.mode === 'ma' && (
              <>
                <input
                  type="number"
                  min={2}
                  max={500}
                  value={decoded.period ?? 50}
                  onChange={e => onChange({ ...rule, param: encodeParam('ma', parseInt(e.target.value) || 50, decoded.type) })}
                  style={{ ...styles.ruleSelect, width: 45 }}
                />
                <select
                  value={decoded.type ?? 'ema'}
                  onChange={e => onChange({ ...rule, param: encodeParam('ma', decoded.period, e.target.value) })}
                  style={{ ...styles.ruleSelect, width: 55 }}
                >
                  <option value="sma">SMA</option>
                  <option value="ema">EMA</option>
                </select>
              </>
            )}
            {decoded.mode === 'bb' && (
              <>
                <select
                  value={decoded.band ?? 'upper'}
                  onChange={e => onChange({ ...rule, param: encodeParam('bb', decoded.period, undefined, { std: decoded.std, band: e.target.value }) })}
                  style={styles.ruleSelect}
                >
                  <option value="upper">Upper</option>
                  <option value="lower">Lower</option>
                  <option value="middle">Middle</option>
                </select>
                <input
                  type="number"
                  min={2}
                  max={500}
                  value={decoded.period ?? 20}
                  onChange={e => onChange({ ...rule, param: encodeParam('bb', parseInt(e.target.value) || 20, undefined, { std: decoded.std, band: decoded.band }) })}
                  style={{ ...styles.ruleSelect, width: 45 }}
                />
                <input
                  type="number"
                  min={0.5}
                  max={5}
                  step={0.5}
                  value={decoded.std ?? 2}
                  onChange={e => onChange({ ...rule, param: encodeParam('bb', decoded.period, undefined, { std: parseFloat(e.target.value) || 2, band: decoded.band }) })}
                  style={{ ...styles.ruleSelect, width: 40 }}
                  title="Std deviations"
                />
              </>
            )}
            {decoded.mode === 'volume_sma' && (
              <input
                type="number"
                min={2}
                max={500}
                value={decoded.period ?? 20}
                onChange={e => onChange({ ...rule, param: encodeParam('volume_sma', parseInt(e.target.value) || 20) })}
                style={{ ...styles.ruleSelect, width: 45 }}
              />
            )}
          </>
        )
      })()}
      {needsValue && (
        <input
          type="number"
          value={rule.value ?? ''}
          onChange={e => onChange({ ...rule, value: parseFloat(e.target.value) })}
          placeholder="Value"
          style={{ ...styles.ruleSelect, width: 70 }}
        />
      )}
      {optionalValue && (
        <>
          <input
            type="number"
            value={rule.value ?? ''}
            onChange={e => {
              const v = e.target.value
              onChange({ ...rule, value: v === '' ? undefined : parseInt(v, 10) })
            }}
            placeholder="N bars"
            min={1}
            step={1}
            style={{ ...styles.ruleSelect, width: 62 }}
          />
          <input
            type="number"
            value={rule.threshold ?? ''}
            onChange={e => {
              const v = e.target.value
              onChange({ ...rule, threshold: v === '' ? undefined : parseFloat(v) })
            }}
            placeholder="min %"
            min={0}
            step={0.01}
            style={{ ...styles.ruleSelect, width: 58 }}
          />
        </>
      )}
      <button onClick={onDelete} style={{ color: '#f85149', padding: '4px 6px' }}><Trash2 size={13} /></button>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  ruleRow: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 },
  ruleSelect: { fontSize: 12, padding: '3px 6px', background: '#0d1117', border: '1px solid #30363d', borderRadius: 4, color: '#e6edf3' },
}
