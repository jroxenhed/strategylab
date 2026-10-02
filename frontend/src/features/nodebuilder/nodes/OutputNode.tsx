/**
 * OutputNode: the terminal cards (near-white stripe), spec S32b.
 *
 * Terminals are the last nodes in an Output Group; each hands one thing to
 * the simulator: entry, exit, size, stop, trailing stop, time stop, regime.
 * They look alike so a row of them reads like a form:
 * - 118px wide, name = the terminal type (`entry`, `trailing`, `time stop`),
 *   one input port, no output port.
 * - The type slot carries a short note: the side in a `regime_switch`
 *   group (`long` / `short`), `>+1.0 %` for a trailing stop that waits for
 *   profit, `const` for an unwired size or stop that uses its constant.
 * - The signal read shows as a read chip (BaseNode draws attr params). The
 *   other params (constant, pct, source, max bars, on flip) show as param
 *   rows in edit mode. `side` shows only in a `regime_switch` group (on
 *   entry, exit, stop, size, trailing and time stop). The size constant
 *   reads as a percent (1.0 shows 100 %), a zero stop as `none`.
 *
 * The group's direction comes from the frame mapping (`data.groupDirection`,
 * rfMapping.ts), so the read-only view works without the store.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'
import { ParamRows } from './ParamRow'
import { TERMINAL_WIDTH, readsFor, terminalNote, terminalRowParams, terminalRowViews, terminalTitle } from './networkFormat'

export default function OutputNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData & { groupDirection?: string | null }
  const backendType = d.backendType ?? ''
  const catalog = d.catalog
  const params = (d.params ?? {}) as Record<string, unknown>
  const groupDirection = d.groupDirection ?? null
  const editable = d.editable === true

  const reads = catalog?.reads ?? readsFor(backendType)
  const attrParams = new Set((catalog?.params ?? []).filter(p => p.type === 'attr').map(p => p.name))
  const catalogHasSide = (catalog?.params ?? []).some(p => p.name === 'side')
  const rowParams = terminalRowParams(backendType, params, attrParams, groupDirection, catalogHasSide)

  return (
    <BaseNode
      cat="output"
      title={terminalTitle(backendType)}
      subtitle={terminalNote(backendType, params, groupDirection)}
      reads={reads}
      writes={[]}
      display={d.display}
      bypass={d.bypass}
      editable={editable}
      width={TERMINAL_WIDTH}
      hasOutput={false}
    >
      {editable && Object.keys(rowParams).length > 0 && (
        <ParamRows nodeId={id} params={rowParams} paramTypes={catalog?.paramTypes} views={terminalRowViews(backendType)} />
      )}
    </BaseNode>
  )
}
