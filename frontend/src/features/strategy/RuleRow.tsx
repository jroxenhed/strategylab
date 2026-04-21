import { Trash2, VolumeX, Volume2 } from 'lucide-react'
import type { Rule } from '../../shared/types'

export const INDICATORS = ['macd', 'rsi', 'price', 'ma'] as const

export const CONDITIONS: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down', 'crosses_above', 'crosses_below', 'above', 'below'],
  rsi: ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above'],
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ma: ['turns_up', 'turns_down', 'decelerating', 'accelerating', 'rising', 'falling',
       'above', 'below', 'crosses_above', 'crosses_below', 'rising_over', 'falling_over'],
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
}

export const emptyRule = (): Rule => ({ indicator: 'macd', condition: 'crossover_up' })

function indicatorLabel(rule: Rule): string {
  if (rule.indicator === 'ma' && rule.params) {
    return `MA ${rule.params.period} ${(rule.params.type || 'ema').toUpperCase()}`
  }
  return rule.indicator.toUpperCase()
}

export function validateRules(rules: Rule[], label: string): string | null {
  for (const rule of rules.filter(r => !r.muted)) {
    const hasParam = !!rule.param || NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
    const needsValue = NEEDS_VALUE.includes(rule.condition) && !hasParam
    if (needsValue && (typeof rule.value !== 'number' || isNaN(rule.value))) {
      return `${label} rule "${indicatorLabel(rule)} ${CONDITION_LABELS[rule.condition]}" is missing a value`
    }
  }
  return null
}

function decodeParam(param?: string): { mode: 'value' | 'close' | 'ma'; period?: number; type?: string } {
  if (!param) return { mode: 'value' }
  if (param === 'close') return { mode: 'close' }
  if (param.startsWith('ma:')) {
    const parts = param.split(':')
    if (parts.length === 3) {
      const period = parseInt(parts[1])
      return { mode: 'ma', period: isNaN(period) ? 50 : period, type: parts[2] || 'ema' }
    }
  }
  return { mode: 'value' }
}

function encodeParam(mode: string, period?: number, type?: string): string | undefined {
  if (mode === 'close') return 'close'
  if (mode === 'ma') return `ma:${period ?? 50}:${type ?? 'ema'}`
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
          const update: Partial<Rule> = { indicator: newInd, condition: CONDITIONS[newInd][0], value: undefined, param: undefined }
          if (newInd === 'ma') {
            update.params = { period: 20, type: 'ema' }
          } else {
            update.params = undefined
          }
          onChange({ ...rule, ...update })
        }}
        style={styles.ruleSelect}
      >
        {INDICATORS.map(i => <option key={i} value={i}>{i.toUpperCase()}</option>)}
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
      <select value={rule.condition} onChange={e => onChange({ ...rule, condition: e.target.value, param: undefined })} style={styles.ruleSelect}>
        {conditions.map(c => <option key={c} value={c}>{CONDITION_LABELS[c] || c}</option>)}
      </select>
      {canParam && (() => {
        const decoded = decodeParam(rule.param)
        return (
          <>
            <select
              value={decoded.mode}
              onChange={e => onChange({ ...rule, param: encodeParam(e.target.value, decoded.period, decoded.type), value: e.target.value === 'value' ? rule.value : undefined })}
              style={styles.ruleSelect}
            >
              <option value="value">Value</option>
              <option value="close">Price</option>
              <option value="ma">MA</option>
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
