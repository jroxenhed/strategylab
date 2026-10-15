/**
 * WrangleNode: the Level 3 code node (F435 W7, spec S46).
 *
 * Header `{}` in the code color, then the body in this order: the code
 * block (always visible, up to 8 lines, highlighted static text, never
 * Monaco), the spare params (BaseNode draws them, at most 4), the chips
 * (reads grey, writes purple, from `parse_code`, never from the text), and
 * the sparkline. Inputs `in0..in3` with one dashed spare port come from the
 * catalog PortsSpec through BaseNode.
 *
 * A click on the block opens the Inspector's Code section at that line.
 * A diagnostic with a line tints that line and turns the block border red.
 */

import type { NodeProps } from '@xyflow/react'
import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { BaseNode, type BaseNodeData } from './BaseNode'
import { CodeBlock } from '../code/CodeDrawer'
import { CODE_SLOT, useCodeDiagnostics, useCodeEnabled, useEnsureParsed, useLastOkParse, useParse } from '../code/codeStore'
import { lineCount } from '../code/codeOps'
import { openCodeInInspector, wrangleAriaLabel } from '../code/codeUi'
import { useNodeBuilderStore } from '../store'

const NO_SET: ReadonlySet<string> = new Set()
const NO_DIAGS: Diagnostic[] = []

export default function WrangleNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const node = d.node
  const code = node?.code ?? ''
  const name = node?.name ?? d.name ?? id
  const editable = d.editable === true
  const codeOn = useCodeEnabled()
  const storeEditable = useNodeBuilderStore(s => !!s.graph && !s.graph.readOnly && id in s.graph.nodes)
  useEnsureParsed(id, code, 'wrangle', storeEditable)
  const parse = useParse(id, CODE_SLOT)
  const lastOk = useLastOkParse(id, CODE_SLOT)
  // Chips keep the last good answer while the code has an error (S48 rule).
  const decl = lastOk?.res ?? parse?.res ?? null
  const reads = decl?.reads.map(r => r.name) ?? []
  const writes = decl?.writes.map(w => w.name) ?? []
  // Parse problems plus the server's (attr_missing, ch_cycle, cook errors), UX-4.
  const diagnostics = useCodeDiagnostics(id, CODE_SLOT, parse && parse.code === code ? parse.res.diagnostics : null, true) ?? NO_DIAGS
  const hasError = diagnostics.some(x => x.severity === 'error')
  const missing = new Set(diagnostics.filter(x => x.code === 'attr_missing').map(x => {
    const m = /@[a-z_][a-z0-9_]*/.exec(x.message)
    return m ? m[0] : ''
  }).filter(Boolean))
  const n = lineCount(code)

  return (
    <BaseNode
      cat="code"
      title={name}
      subtitle="wrangle"
      reads={reads}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
      editable={editable}
      width={240}
      missingReads={missing.size ? missing : NO_SET}
      emptyChipsText={decl && writes.length === 0 ? 'writes nothing' : undefined}
      ariaLabel={wrangleAriaLabel(name, writes)}
    >
      <CodeBlock
        code={code}
        maxLines={8}
        diagnostics={diagnostics}
        dim={!codeOn}
        error={hasError}
        ariaLabel={`Open code for ${name}, ${n} line${n === 1 ? '' : 's'}`}
        onOpen={() => openCodeInInspector(id, 1)}
        testId={`nb-codeblk-${id}`}
      />
    </BaseNode>
  )
}
