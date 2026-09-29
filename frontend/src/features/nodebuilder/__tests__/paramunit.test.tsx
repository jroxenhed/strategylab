/**
 * Param unit tests (F435 Wave 0, item 0.X).
 *
 * - unitLabel / formatPercent turn the catalog's ParamTypeSpec.unit into the
 *   text shown after a param input.
 * - ParamRow shows that unit next to the input, and numeric inputs stay
 *   type="text" inputMode="decimal" (F278).
 * - The read-only Position Size chip shows the fraction with its percent,
 *   for example "1 (100%)".
 */

import { describe, it, expect, afterEach, beforeAll } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { ReactFlow, type Node, type NodeTypes } from '@xyflow/react'
import { ParamRow } from '../nodes/ParamRow'
import SettingsNode from '../nodes/SettingsNode'
import { unitLabel, formatPercent } from '../nodes/paramFormat'
import { NODE_CATALOG } from '../catalog'

beforeAll(() => {
  // jsdom has no DOMMatrixReadOnly; React Flow reads it when measuring nodes.
  if (typeof (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly === 'undefined') {
    ;(globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = class {
      m22 = 1
    }
  }
})

afterEach(() => cleanup())

describe('unitLabel / formatPercent', () => {
  it('returns null when there is no unit', () => {
    expect(unitLabel(undefined, '5')).toBeNull()
    expect(unitLabel('', '5')).toBeNull()
  })

  it('passes plain units through', () => {
    expect(unitLabel('%', '5')).toBe('%')
    expect(unitLabel('bps', '2')).toBe('bps')
  })

  it('shows a fraction with its percent', () => {
    expect(unitLabel('fraction', '1')).toBe('fraction (100%)')
    expect(unitLabel('fraction', '0.25')).toBe('fraction (25%)')
    // Not a number yet: just the unit, no made-up percent.
    expect(unitLabel('fraction', '')).toBe('fraction')
    expect(unitLabel('fraction', 'abc')).toBe('fraction')
  })

  it('formats percents without float noise', () => {
    expect(formatPercent(1)).toBe('100%')
    expect(formatPercent(0.1 + 0.2)).toBe('30%')
    expect(formatPercent(0.3333)).toBe('33.33%')
  })
})

describe('ParamRow unit', () => {
  it('renders the catalog unit next to a numeric input', () => {
    render(<ParamRow nodeId="n1" paramKey="pct" value={5} typeSpec={{ type: 'number', unit: '%' }} />)
    const input = screen.getByLabelText(/pct/) as HTMLInputElement
    expect(input.getAttribute('type')).toBe('text')
    expect(input.getAttribute('inputMode') ?? input.getAttribute('inputmode')).toBe('decimal')
    expect(screen.getByTestId('param-unit').textContent).toBe('%')
  })

  it('renders the Position Size fraction with its percent', () => {
    const spec = NODE_CATALOG.find(e => e.name === 'position_size')?.paramTypes?.size
    expect(spec?.unit).toBe('fraction')
    render(<ParamRow nodeId="n1" paramKey="size" value={1} typeSpec={spec} />)
    expect(screen.getByTestId('param-unit').textContent).toBe('fraction (100%)')
  })

  it('renders no unit when the catalog gives none', () => {
    render(<ParamRow nodeId="n1" paramKey="period" value={14} typeSpec={{ type: 'number' }} />)
    expect(screen.queryByTestId('param-unit')).toBeNull()
  })
})

describe('Position Size chip', () => {
  function renderSize(size: number) {
    const nodeTypes: NodeTypes = { settings: SettingsNode }
    const nodes: Node[] = [{
      id: 'size',
      type: 'settings',
      position: { x: 0, y: 0 },
      data: {
        backendType: 'position_size',
        catalog: null,
        params: { size },
        display: false,
        bypass: false,
        nodePath: '/size',
        editable: false,
      },
    }]
    const { container } = render(
      <div style={{ width: 800, height: 600 }}>
        <ReactFlow nodes={nodes} edges={[]} nodeTypes={nodeTypes} />
      </div>,
    )
    return container.textContent ?? ''
  }

  it('shows the fraction with its percent', () => {
    expect(renderSize(1)).toContain('1 (100%)')
  })

  it('shows a partial size the same way', () => {
    expect(renderSize(0.5)).toContain('0.5 (50%)')
  })
})
