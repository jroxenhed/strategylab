/**
 * Code commands (F435 W7, specs S44, S45, S46, S49).
 *
 * - `code.useExpression` (param menu `Use expression =`): the param becomes
 *   an expression holding its current value (`14`, `"sma"`).
 * - `code.useLiteral` (param menu `Use literal value`): back to the value
 *   the param had before code mode, else its default. The expression stays
 *   in undo history.
 * - `code.addBlock` (node menu `Add code block`): opens the Inspector's
 *   Code section on an empty editor.
 * - `code.openWrangle` (Enter on a selected Wrangle): opens its Code
 *   section. Enter dives into a network first (network.dive is newer).
 * With code off on the server the rows are disabled with the reason, and
 * choosing one shows the code-off banner (S49).
 *
 * Loaded by the commands/ auto-registry (index.ts).
 */

import { isExprValue, type Graph } from '../../../api/nodebuilder'
import { getMenuTarget } from '../contextMenuModel'
import type { NodeBuilderState } from '../store'
import { paramSpecsOf } from '../streamLabels'
import { addCodeBlock, canHaveCode, openCodeInInspector } from '../code/codeUi'
import { initialExprText, isCodeableParam, literalFor, setParamExpr, setParamLiteral, WRANGLE_TYPE } from '../code/codeOps'
import { requestCodeBanner, useCodeStore } from '../code/codeStore'
import type { Command } from './index'

const codeOn = () => useCodeStore.getState().enabled

/** The menu's param on the editable store graph, or null. */
function target(s: NodeBuilderState) {
  const t = getMenuTarget()
  const g: Graph | null = s.graph
  if (!t || !g || g.readOnly) return null
  const node = g.nodes[t.nodeId]
  if (!node) return null
  const spec = paramSpecsOf(node.type).find(p => p.name === t.param)
    ?? (node.spare_params ?? []).map(sp => ({ name: sp.name, type: sp.type === 'float' ? 'number' as const : sp.type === 'int' ? 'int' as const : sp.type === 'bool' ? 'bool' as const : 'string' as const, label: sp.label, default: sp.default }))
      .find(p => p.name === t.param)
  return { t, node, spec, value: node.params[t.param] ?? spec?.default }
}

function expressionProblem(s: NodeBuilderState): string | null {
  const p = target(s)
  if (!p) return 'No editable param'
  if (!isCodeableParam(p.node.type, p.spec, p.t.param)) return 'This param cannot hold code'
  if (!codeOn()) return 'disabled'
  if (isExprValue(p.value)) return 'Already an expression'
  return null
}

function literalProblem(s: NodeBuilderState): string | null {
  const p = target(s)
  if (!p) return 'No editable param'
  if (!isExprValue(p.value)) return 'Not an expression'
  return null
}

function selectedOne(s: NodeBuilderState) {
  if (s.selectedNodeIds.length !== 1 || !s.graph) return null
  return s.graph.nodes[s.selectedNodeIds[0]] ?? null
}

function addBlockProblem(s: NodeBuilderState): string | null {
  const node = selectedOne(s)
  if (!node || !s.graph || s.graph.readOnly) return 'Select one node first'
  if (!canHaveCode(node) || node.type === WRANGLE_TYPE) return 'This node has no code block'
  if (node.code && node.code.trim()) return 'Already has a code block'
  if (!codeOn()) return 'disabled'
  return null
}

export const commands: Command[] = [
  {
    id: 'code.useExpression',
    label: 'Use expression =',
    menu: 'param',
    menuSlot: 'param',
    when: s => expressionProblem(s) === null,
    disabledReason: expressionProblem,
    run: ({ store }) => {
      const s = store.getState()
      if (!codeOn()) { requestCodeBanner(); return false }
      const p = target(s)
      if (!p || expressionProblem(s) !== null) return false
      s.commit(`edit ${p.t.param}`, g => setParamExpr(g, p.t.nodeId, p.t.param, initialExprText(p.value)))
    },
  },
  {
    id: 'code.useLiteral',
    label: 'Use literal value',
    menu: 'param',
    menuSlot: 'param',
    when: s => literalProblem(s) === null,
    disabledReason: literalProblem,
    run: ({ store }) => {
      const s = store.getState()
      const p = target(s)
      if (!p || literalProblem(s) !== null) return false
      const lit = literalFor(p.node, p.t.param, p.spec?.default ?? null)
      s.commit(`edit ${p.t.param}`, g => setParamLiteral(g, p.t.nodeId, p.t.param, lit))
    },
  },
  {
    id: 'code.addBlock',
    label: 'Add code block',
    menu: 'node',
    when: s => addBlockProblem(s) === null,
    disabledReason: addBlockProblem,
    run: ({ store }) => {
      const s = store.getState()
      if (!codeOn()) { requestCodeBanner(); return false }
      const node = selectedOne(s)
      if (!node || addBlockProblem(s) !== null) return false
      addCodeBlock(node.id)
      openCodeInInspector(node.id, 1)
    },
  },
  {
    id: 'code.openWrangle',
    label: 'Open code',
    keys: ['enter'],
    readOnlyOk: true,
    when: s => selectedOne(s)?.type === WRANGLE_TYPE,
    run: ({ store, event }) => {
      const t = event?.target
      if (t instanceof Element && t.closest('input, textarea, select, button, [role="button"], [contenteditable="true"]')) return false
      const node = selectedOne(store.getState())
      if (!node || node.type !== WRANGLE_TYPE) return false
      openCodeInInspector(node.id, 1)
    },
  },
]
