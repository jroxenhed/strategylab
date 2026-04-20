import { useState, useEffect, useRef, useCallback } from 'react'
import type { IndicatorInstance, IndicatorType, ParamFieldNumber, ParamFieldSelect } from '../../shared/types'
import { INDICATOR_DEFS, createInstance, paramSummary } from '../../shared/types/indicators'

interface IndicatorListProps {
  indicators: IndicatorInstance[]
  onChange: React.Dispatch<React.SetStateAction<IndicatorInstance[]>>
}

const AVAILABLE_TYPES: IndicatorType[] = ['rsi', 'macd', 'bb', 'atr', 'ma', 'volume']

function NumberParamInput({ field, value, onChange, onCommit }: {
  field: ParamFieldNumber
  value: number
  onChange: (v: number) => void
  onCommit: (v: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  const focused = useRef(false)

  useEffect(() => {
    if (!focused.current) setDraft(String(value))
  }, [value])

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 40 }}>{field.label}</span>
      <input
        type="number"
        value={draft}
        min={field.min}
        max={field.max}
        onFocus={() => { focused.current = true }}
        onChange={e => {
          setDraft(e.target.value)
          const v = parseFloat(e.target.value)
          if (!isNaN(v)) onChange(v)
        }}
        onBlur={e => {
          focused.current = false
          const v = parseFloat(e.target.value)
          if (!isNaN(v)) onCommit(v)
        }}
        style={{
          width: 48, background: 'var(--bg-main)', border: '1px solid var(--border-light)',
          borderRadius: 3, color: 'var(--text-primary)', padding: '2px 6px', fontSize: 11,
        }}
      />
    </div>
  )
}

export default function IndicatorList({ indicators, onChange }: IndicatorListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [showAddMenu, setShowAddMenu] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => () => clearTimeout(debounceRef.current), [])

  useEffect(() => {
    if (!showAddMenu) return
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setShowAddMenu(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [showAddMenu])

  function toggle(id: string) {
    onChange(prev => prev.map(i => i.id === id ? { ...i, enabled: !i.enabled } : i))
  }

  function remove(id: string) {
    onChange(prev => prev.filter(i => i.id !== id))
    if (expandedId === id) setExpandedId(null)
  }

  const updateParam = useCallback((id: string, key: string, value: number | string) => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      onChange(prev => prev.map(i => i.id === id ? { ...i, params: { ...i.params, [key]: value } } : i))
    }, 300)
  }, [onChange])

  function addIndicator(type: IndicatorType) {
    onChange(prev => [...prev, createInstance(type)])
    setShowAddMenu(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', marginBottom: 12 }}>
        <div ref={menuRef} style={{ position: 'relative' }}>
          <button
            onClick={() => setShowAddMenu(!showAddMenu)}
            style={{
              background: 'var(--bg-input)', color: 'var(--accent-primary)',
              border: '1px solid var(--border-light)', borderRadius: 4,
              padding: '3px 10px', fontSize: 11, cursor: 'pointer',
            }}
          >
            + Add
          </button>
          {showAddMenu && (
            <div style={{
              position: 'absolute', top: '100%', right: 0, marginTop: 4,
              background: 'var(--bg-panel)', border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)', zIndex: 100, minWidth: 140,
              boxShadow: 'var(--shadow-md)', overflow: 'hidden',
            }}>
              {AVAILABLE_TYPES.map(type => (
                <div
                  key={type}
                  onClick={() => addIndicator(type)}
                  style={{
                    padding: '8px 12px', cursor: 'pointer', fontSize: 12,
                    color: 'var(--text-primary)', borderBottom: '1px solid var(--border-light)',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-panel-hover)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  {INDICATOR_DEFS[type].label}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {indicators.map(inst => {
        const def = INDICATOR_DEFS[inst.type]
        const isExpanded = expandedId === inst.id
        const summary = paramSummary(inst)

        return (
          <div key={inst.id} style={{
            background: 'var(--bg-input)', borderRadius: 6, padding: '8px 10px',
            marginBottom: 6,
            border: isExpanded ? '1px solid var(--border-light)' : '1px solid transparent',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={inst.enabled}
                onChange={() => toggle(inst.id)}
                style={{ accentColor: inst.color ?? 'var(--accent-primary)', margin: 0 }}
              />
              <span style={{ flex: 1, fontSize: 12, color: inst.enabled ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {def.label}
              </span>
              {summary && (
                <span style={{ fontSize: 10, color: 'var(--text-muted)', marginRight: 4 }}>{summary}</span>
              )}
              {def.paramFields.length > 0 && (
                <span
                  onClick={() => setExpandedId(isExpanded ? null : inst.id)}
                  style={{ cursor: 'pointer', color: isExpanded ? 'var(--accent-primary)' : 'var(--text-muted)', fontSize: 13 }}
                  title="Settings"
                >
                  ⚙
                </span>
              )}
              <span
                onClick={() => remove(inst.id)}
                style={{ cursor: 'pointer', color: 'var(--text-muted)', fontSize: 11 }}
                title="Remove"
              >
                ✕
              </span>
            </div>

            {isExpanded && def.paramFields.length > 0 && (
              <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {def.paramFields.map(field => {
                  if (field.kind === 'select') {
                    const selectField = field as ParamFieldSelect
                    return (
                      <div key={field.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 40 }}>{field.label}</span>
                        <select
                          value={String(inst.params[field.key] ?? selectField.options[0]?.value)}
                          onChange={e => {
                            clearTimeout(debounceRef.current)
                            onChange(prev => prev.map(i => i.id === inst.id ? { ...i, params: { ...i.params, [field.key]: e.target.value } } : i))
                          }}
                          style={{ fontSize: 11, background: 'var(--bg-main)', border: '1px solid var(--border-light)', borderRadius: 3, color: 'var(--text-primary)', padding: '2px 6px' }}
                        >
                          {selectField.options.map(opt => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      </div>
                    )
                  }
                  return (
                    <NumberParamInput
                      key={field.key}
                      field={field}
                      value={Number(inst.params[field.key] ?? 0)}
                      onChange={v => updateParam(inst.id, field.key, v)}
                      onCommit={v => {
                        clearTimeout(debounceRef.current)
                        const numField = field as ParamFieldNumber
                        const clamped = Math.max(numField.min ?? -Infinity, Math.min(numField.max ?? Infinity, v))
                        onChange(prev => prev.map(i => i.id === inst.id ? { ...i, params: { ...i.params, [field.key]: clamped } } : i))
                      }}
                    />
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
