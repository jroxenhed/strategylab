/**
 * React Flow node types for the canvas (W3 pre-step 3.0).
 *
 * The built-in renderers are registered here. Other items add theirs from
 * their own module with `registerNodeType(name, Component)`, for example
 * `registerNodeType('nbBox', NetworkBox)`. Import that module from your
 * plugin or command module (both folders load automatically) so the type is
 * registered before the canvas first renders.
 *
 * Each renderer is wrapped in React.memo, so a single node move doesn't
 * re-render every other node. The default `arePropsEqual` is fine because
 * the canvas keeps each node's `data` object until that node changes.
 *
 * The map keeps one identity until a type is registered: React Flow warns
 * when `nodeTypes` changes between renders.
 *
 * The Entry/Exit renderer is registered as 'nbOutput', not 'output': React
 * Flow has a built-in 'output' node type with a white default style that
 * would otherwise be applied to it. This name is only the React Flow type;
 * the graph node type is unchanged. Use an `nb` prefix for new names too.
 */

import { memo, useSyncExternalStore } from 'react'
import type { NodeTypes } from '@xyflow/react'
import TickerNode from './nodes/TickerNode'
import IndicatorNode from './nodes/IndicatorNode'
import ComparisonNode from './nodes/ComparisonNode'
import LogicNode from './nodes/LogicNode'
import SettingsNode from './nodes/SettingsNode'
import OutputNode from './nodes/OutputNode'
import { KeyedStack } from './keyedStack'

/** A React Flow node renderer. */
export type NodeComponent = NodeTypes[string]

// Per name a stack of renderers (keyedStack.ts, EA-12): an override is
// removed by its own unregister and the built-in comes back.
const registry = new KeyedStack<NodeComponent>()
for (const [name, C] of Object.entries({
  ticker: TickerNode,
  indicator: IndicatorNode,
  comparison: ComparisonNode,
  logic: LogicNode,
  settings: SettingsNode,
  nbOutput: OutputNode,
})) registry.push(name, memo(C) as NodeComponent)

let current: NodeTypes = Object.fromEntries(registry.entries())

const listeners = new Set<() => void>()
function notify() {
  for (const l of [...listeners]) l()
}

/**
 * Add (or replace) a React Flow node type. Returns a function that removes
 * it again (for tests).
 */
export function registerNodeType(name: string, Component: NodeComponent): () => void {
  const wrapped = memo(Component) as NodeComponent
  const remove = registry.push(name, wrapped)
  current = Object.fromEntries(registry.entries())
  notify()
  return () => {
    if (!remove()) return
    current = Object.fromEntries(registry.entries())
    notify()
  }
}

/** The node type map (same object until a registration changes it). */
export function getNodeTypes(): NodeTypes {
  return current
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** The node type map, re-rendering the caller when a type is registered. */
export function useNodeTypes(): NodeTypes {
  return useSyncExternalStore(subscribe, getNodeTypes)
}
