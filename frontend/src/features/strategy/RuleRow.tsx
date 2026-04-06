import { Trash2 } from 'lucide-react'
import type { Rule } from '../../shared/types'

export const INDICATORS = ['macd', 'rsi', 'price', 'ema20', 'ema50', 'ema200'] as const
export const CONDITIONS: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down', 'crosses_above', 'crosses_below', 'above', 'below'],
  rsi: ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above'],
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema20: ['above', 'below', 'rising', 'falling', 'rising_over', 'falling_over'],
  ema50: ['above', 'below', 'rising', 'falling', 'rising_over', 'falling_over'],
  ema200: ['above', 'below', 'rising', 'falling', 'rising_over', 'falling_over'],
}
export const CONDITION_LABELS: Record<string, string> = {
  crossover_up: 'Crosses above signal',
  crossover_down: 'Crosses below signal',
  crosses_above: 'Crosses above value',
  crosses_below: 'Crosses below value',
  above: 'Is above',
  below: 'Is below',
  turns_up_below: 'Turns up from below',
  turns_down_above: 'Turns down from above',
  rising: 'Is rising',
  falling: 'Is falling',
  rising_over: 'Rising over N bars',
  falling_over: 'Falling over N bars',
}
export const NEEDS_VALUE = ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above', 'rising_over', 'falling_over']
export const NEEDS_PARAM: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down'],
}
export const CAN_USE_PARAM: Record<string, string[]> = {
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema20: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema50: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema200: ['above', 'below', 'crosses_above', 'crosses_below'],
}
export const PARAM_OPTIONS = [
  { value: 'ema20', label: 'EMA 20' },
  { value: 'ema50', label: 'EMA 50' },
  { value: 'ema200', label: 'EMA 200' },
  { value: 'close', label: 'Price' },
]

export const emptyRule = (): Rule => ({ indicator: 'macd', condition: 'crossover_up' })

export function validateRules(rules: Rule[], label: string): string | null {
  for (const rule of rules) {
    const hasParam = !!rule.param || NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
    const needsValue = NEEDS_VALUE.includes(rule.condition) && !hasParam
    if (needsValue && (typeof rule.value !== 'number' || isNaN(rule.value))) {
      return `${label} rule "${rule.indicator.toUpperCase()} ${CONDITION_LABELS[rule.condition]}" is missing a value`
    }
  }
  return null
}

export default function RuleRow({ rule, onChange, onDelete }: { rule: Rule; onChange: (r: Rule) => void; onDelete: () => void }) {
  const conditions = CONDITIONS[rule.indicator] || []
  const canParam = CAN_USE_PARAM[rule.indicator]?.includes(rule.condition)
  const hasParam = canParam && !!rule.param
  const forcedParam = NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
  const needsValue = NEEDS_VALUE.includes(rule.condition) && !forcedParam && !hasParam

  const paramOptions = PARAM_OPTIONS.filter(p => {
    if (rule.indicator === 'price' && p.value === 'close') return false
    if (rule.indicator === p.value) return false
    return true
  })

  return (
    <div style={styles.ruleRow}>
      <select value={rule.indicator} onChange={e => onChange({ ...rule, indicator: e.target.value as Rule['indicator'], condition: CONDITIONS[e.target.value][0] as any, value: undefined, param: undefined })} style={styles.ruleSelect}>
        {INDICATORS.map(i => <option key={i} value={i}>{i.toUpperCase()}</option>)}
      </select>
      <select value={rule.condition} onChange={e => onChange({ ...rule, condition: e.target.value as any, param: undefined })} style={styles.ruleSelect}>
        {conditions.map(c => <option key={c} value={c}>{CONDITION_LABELS[c] || c}</option>)}
      </select>
      {canParam && (
        <select
          value={rule.param ?? '_value'}
          onChange={e => {
            const v = e.target.value
            if (v === '_value') onChange({ ...rule, param: undefined })
            else onChange({ ...rule, param: v, value: undefined })
          }}
          style={styles.ruleSelect}
        >
          <option value="_value">Value</option>
          {paramOptions.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
      )}
      {needsValue && (
        <input
          type="number"
          value={rule.value ?? ''}
          onChange={e => onChange({ ...rule, value: parseFloat(e.target.value) })}
          placeholder="Value"
          style={{ ...styles.ruleSelect, width: 70 }}
        />
      )}
      <button onClick={onDelete} style={{ color: '#f85149', padding: '4px 6px' }}><Trash2 size={13} /></button>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  ruleRow: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 },
  ruleSelect: { fontSize: 12, padding: '3px 6px', background: '#0d1117', border: '1px solid #30363d', borderRadius: 4, color: '#e6edf3' },
}
