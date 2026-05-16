import { useState, useEffect, useRef, useCallback } from 'react'
import type { IndicatorInstance, IndicatorType, ParamFieldNumber, ParamFieldSelect } from '../../shared/types'
import { INDICATOR_DEFS, createInstance, paramSummary } from '../../shared/types/indicators'

interface IndicatorListProps {
  indicators: IndicatorInstance[]
  onChange: React.Dispatch<React.SetStateAction<IndicatorInstance[]>>
}

const AVAILABLE_TYPES: IndicatorType[] = ['rsi', 'macd', 'bb', 'atr', 'ma', 'volume', 'stochastic', 'vwap', 'adx']

// (a) PRESET_COLORS — 14 Tailwind 500-tone colors (lines 13–28)
const PRESET_COLORS = [
  '#ef4444', // red-500
  '#f97316', // orange-500
  '#f59e0b', // amber-500
  '#eab308', // yellow-500
  '#84cc16', // lime-500
  '#22c55e', // green-500
  '#10b981', // emerald-500
  '#14b8a6', // teal-500
  '#06b6d4', // cyan-500
  '#0ea5e9', // sky-500
  '#3b82f6', // blue-500
  '#8b5cf6', // violet-500
  '#d946ef', // fuchsia-500
  '#ec4899', // pink-500
]
const SUPPORTS_COLOR = new Set<IndicatorType>(['ma', 'rsi', 'atr', 'macd'])

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

// (c) ColorPicker — preset swatches + custom hex input (lines 72–108)
function ColorPicker({ color, onSelect }: { color: string; onSelect: (c: string) => void }) {
  const [draft, setDraft] = useState(color)
  const [invalid, setInvalid] = useState(false)

  // Keep draft in sync when color changes externally (e.g. another picker click)
  useEffect(() => { setDraft(color); setInvalid(false) }, [color])

  const HEX_RE = /^#[0-9a-fA-F]{6}$/

  function commitDraft(v: string) {
    const trimmed = v.trim()
    if (HEX_RE.test(trimmed)) {
      setInvalid(false)
      onSelect(trimmed.toLowerCase())
    } else {
      // Revert draft to last valid color
      setInvalid(true)
      setTimeout(() => { setDraft(color); setInvalid(false) }, 800)
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 40 }}>Color</span>
      {PRESET_COLORS.map(c => (
        <span
          key={c}
          onClick={() => onSelect(c)}
          style={{
            width: 13, height: 13, borderRadius: '50%', background: c, cursor: 'pointer',
            border: color === c ? '2px solid var(--text-primary)' : '2px solid transparent',
            flexShrink: 0,
          }}
        />
      ))}
      {/* Custom hex input — 6-char wide, validates on blur */}
      <input
        type="text"
        value={draft}
        maxLength={7}
        onChange={e => { setDraft(e.target.value); setInvalid(false) }}
        onBlur={() => commitDraft(draft)}
        onKeyDown={e => { if (e.key === 'Enter') commitDraft(draft) }}
        title="Custom hex color (#rrggbb)"
        style={{
          width: 52, fontSize: 10, padding: '1px 4px',
          background: 'var(--bg-main)',
          border: `1px solid ${invalid ? '#ef4444' : 'var(--border-light)'}`,
          borderRadius: 3, color: 'var(--text-primary)',
          fontFamily: 'monospace',
        }}
      />
    </div>
  )
}

export default function IndicatorList({ indicators, onChange }: IndicatorListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [colorPickerId, setColorPickerId] = useState<string | null>(null)
  const colorPopoverRef = useRef<HTMLDivElement>(null)
  const [showAddMenu, setShowAddMenu] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const dragIdRef = useRef<string | null>(null)
  const [dragOverId, setDragOverId] = useState<string | null>(null)

  // Close color popover on outside click
  useEffect(() => {
    if (!colorPickerId) return
    function handle(e: MouseEvent) {
      if (colorPopoverRef.current && !colorPopoverRef.current.contains(e.target as Node)) {
        setColorPickerId(null)
      }
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [colorPickerId])

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

  function handleDragStart(id: string) {
    dragIdRef.current = id
  }

  function handleDragOver(e: React.DragEvent, id: string) {
    e.preventDefault()
    setDragOverId(id)
  }

  function handleDrop(e: React.DragEvent, targetId: string) {
    e.preventDefault()
    const sourceId = dragIdRef.current
    if (!sourceId || sourceId === targetId) {
      setDragOverId(null)
      dragIdRef.current = null
      return
    }
    onChange(prev => {
      const from = prev.findIndex(i => i.id === sourceId)
      const to = prev.findIndex(i => i.id === targetId)
      if (from === -1 || to === -1) return prev
      const next = [...prev]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
    setDragOverId(null)
    dragIdRef.current = null
  }

  function handleDragEnd() {
    setDragOverId(null)
    dragIdRef.current = null
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
        // Color is in its own popover now — only count params/main-pane TF for expand.
        const hasSettings = def.paramFields.length > 0 || def.pane === 'main'

        return (
          <div
            key={inst.id}
            draggable
            onDragStart={() => handleDragStart(inst.id)}
            onDragOver={e => handleDragOver(e, inst.id)}
            onDrop={e => handleDrop(e, inst.id)}
            onDragEnd={handleDragEnd}
            style={{
              background: dragOverId === inst.id ? 'var(--bg-panel-hover)' : 'var(--bg-input)',
              borderRadius: 6,
              // (b) Denser collapsed row — 4px vertical padding → ≤24px total row height (lines 108–111)
              padding: isExpanded ? '6px 10px' : '3px 8px',
              marginBottom: 4,
              border: dragOverId === inst.id
                ? '1px solid var(--accent-primary)'
                : isExpanded ? '1px solid var(--border-light)' : '1px solid transparent',
              outline: 'none',
              position: 'relative',
            }}
          >
            {/* (b) Collapsed row: single-line summary "RSI · 14 · Wilder ▾" at font-size 11 (lines 113–145) */}
            <div
              style={{ display: 'flex', alignItems: 'center', gap: 6, minHeight: 18, cursor: hasSettings ? 'pointer' : 'default' }}
              onClick={() => hasSettings && setExpandedId(isExpanded ? null : inst.id)}
            >
              {/* Drag handle — initiates drag, does not toggle expand */}
              <span
                onMouseDown={e => e.stopPropagation()}
                style={{
                  cursor: 'grab', color: 'var(--text-muted)', fontSize: 11,
                  flexShrink: 0, userSelect: 'none', lineHeight: 1,
                }}
                title="Drag to reorder"
              >
                ⋮⋮
              </span>
              <input
                type="checkbox"
                checked={inst.enabled}
                onChange={e => { e.stopPropagation(); toggle(inst.id) }}
                onClick={e => e.stopPropagation()}
                style={{ accentColor: inst.color ?? 'var(--accent-primary)', margin: 0, width: 11, height: 11 }}
              />
              {/* Label + summary in one truncated line */}
              <span style={{
                flex: 1, fontSize: 11, lineHeight: '16px',
                color: inst.enabled ? 'var(--text-primary)' : 'var(--text-muted)',
                overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
              }}>
                {def.label}{summary ? ` · ${summary}` : ''}
              </span>
              {/* Color swatch — popover trigger; sits right-of-params, left-of-chevron/✕ */}
              {SUPPORTS_COLOR.has(inst.type) && (
                <span
                  onClick={e => {
                    e.stopPropagation()
                    setColorPickerId(colorPickerId === inst.id ? null : inst.id)
                  }}
                  style={{
                    width: 12, height: 12, borderRadius: '50%',
                    background: inst.color ?? PRESET_COLORS[0],
                    flexShrink: 0, cursor: 'pointer',
                    border: '1px solid var(--border-light)',
                  }}
                  title="Pick color"
                />
              )}
              {hasSettings && (
                <span style={{ fontSize: 9, color: 'var(--text-muted)', flexShrink: 0 }}>
                  {isExpanded ? '▴' : '▾'}
                </span>
              )}
              <span
                onClick={e => { e.stopPropagation(); remove(inst.id) }}
                style={{ cursor: 'pointer', color: 'var(--text-muted)', fontSize: 10, flexShrink: 0 }}
                title="Remove"
              >
                ✕
              </span>
            </div>

            {/* Color popover — anchored to the row, opens to the right */}
            {colorPickerId === inst.id && SUPPORTS_COLOR.has(inst.type) && (
              <div
                ref={colorPopoverRef}
                onClick={e => e.stopPropagation()}
                style={{
                  position: 'absolute', zIndex: 100,
                  marginTop: 4, padding: 8,
                  background: 'var(--bg-panel)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 6,
                  boxShadow: 'var(--shadow-md)',
                  right: 8,
                }}
              >
                <ColorPicker
                  color={inst.color ?? PRESET_COLORS[0]}
                  onSelect={c => {
                    onChange(prev => prev.map(i => i.id === inst.id ? { ...i, color: c } : i))
                    setColorPickerId(null)
                  }}
                />
              </div>
            )}

            {isExpanded && hasSettings && (
              <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {def.pane === 'main' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 40 }}>TF</span>
                    <select
                      value={inst.htfInterval ?? 'same'}
                      onChange={e => {
                        const v = e.target.value
                        onChange(prev => prev.map(i =>
                          i.id === inst.id ? { ...i, htfInterval: v === 'same' ? undefined : v } : i
                        ))
                      }}
                      style={{ fontSize: 11, background: 'var(--bg-main)', border: '1px solid var(--border-light)', borderRadius: 3, color: 'var(--text-primary)', padding: '2px 6px' }}
                    >
                      <option value="same">Same</option>
                      <option value="1d">1D</option>
                      <option value="1wk">1W</option>
                    </select>
                  </div>
                )}
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
