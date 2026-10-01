/**
 * Test-only catalog entries shaped like the Wave 2 catalog (F435 2.E).
 *
 * The real catalog gains `attr`, `attr_list`, `write` and `time_range`
 * params when the orchestrator regenerates catalog.generated.ts. These
 * entries (type names start with `t2_`, so they never clash) let the tests
 * prove the ports and stream UI handle such nodes from PortsSpec and
 * ParamSpec alone, with no code that knows a type name.
 *
 * Use it from a test with:
 *   vi.mock('../catalog.generated', async orig => {
 *     const real = await orig<typeof import('../catalog.generated')>()
 *     const { W2_TEST_ENTRIES } = await import('./w2Catalog.fixture')
 *     return { ...real, GENERATED_CATALOG: [...real.GENERATED_CATALOG, ...W2_TEST_ENTRIES] }
 *   })
 *
 * Types only are imported here, so the fixture can load inside a mock.
 */

import type { GeneratedCatalogEntry, ParamSpec } from '../catalog.generated'

const base = { compile_active: true, reads: [], writes: [], subtitle: null, setting_key: null, ins: 1, outs: 1 }

function write(name: string, def: string, dtype: 'float' | 'bool' = 'float'): ParamSpec {
  return { name, type: 'write', label: name, default: def, dtype }
}

export const W2_TEST_ENTRIES: GeneratedCatalogEntry[] = [
  {
    ...base,
    name: 't2_ind',
    cat: 'indicator',
    desc: 'Test indicator that reads one source.',
    inputs: { ports: [{ label: 'source' }], dynamic: false, min: 1, max: 1 },
    params: [
      { name: 'period', type: 'int', label: 'period', default: 14, min: 2, max: 500 },
      { name: 'source', type: 'attr', label: 'source', default: null, dtype: 'float' },
      write('out', '@t2'),
    ],
  },
  {
    ...base,
    name: 't2_cmp',
    cat: 'comparison',
    desc: 'Test comparison with operands a and b.',
    inputs: { ports: [{ label: 'a' }, { label: 'b', optional: true }], dynamic: false, min: 1, max: 2 },
    params: [
      { name: 'a', type: 'attr', label: 'a', default: null, dtype: 'float' },
      { name: 'b', type: 'attr', label: 'b', default: null, dtype: 'float', optional: true },
      write('out', '@cmp', 'bool'),
    ],
  },
  {
    ...base,
    name: 't2_and',
    cat: 'logic',
    desc: 'Test logic node with dynamic inputs.',
    inputs: { ports: [{ label: 'in0' }, { label: 'in1', optional: true }], dynamic: true, min: 1, max: 4 },
    params: [
      { name: 'terms', type: 'attr_list', label: 'terms', default: [], dtype: 'bool' },
      write('out', '@all', 'bool'),
    ],
  },
  {
    ...base,
    name: 't2_macd',
    cat: 'indicator',
    desc: 'Test node with three writes.',
    inputs: { ports: [{ label: 'source' }], dynamic: false, min: 1, max: 1 },
    params: [
      { name: 'source', type: 'attr', label: 'source', default: null, dtype: 'float' },
      write('out_line', '@macd'),
      write('out_signal', '@macd_signal'),
      write('out_hist', '@macd_hist'),
    ],
  },
  {
    ...base,
    name: 't2_tod',
    cat: 'data',
    desc: 'Test time-of-day filter.',
    inputs: { ports: [{ label: 'in0' }], dynamic: false, min: 1, max: 1 },
    params: [
      { name: 'range', type: 'time_range', label: 'range', default: null },
      write('out', '@in_window', 'bool'),
    ],
  },
  {
    ...base,
    name: 't2_dow',
    cat: 'data',
    desc: 'Test day-of-week filter.',
    inputs: { ports: [{ label: 'in0' }], dynamic: false, min: 1, max: 1 },
    params: [
      {
        name: 'days',
        type: 'select',
        label: 'days',
        default: ['mon', 'tue', 'wed', 'thu', 'fri'],
        options: ['mon', 'tue', 'wed', 'thu', 'fri'],
      },
      write('out', '@on_day', 'bool'),
    ],
  },
]
