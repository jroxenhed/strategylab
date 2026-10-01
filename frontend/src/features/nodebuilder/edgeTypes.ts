/**
 * React Flow edge types for the canvas (W3 pre-step 3.0).
 *
 * The built-in wire renderer ('attr', edges/AttrEdge.tsx) is registered
 * here. Other items add theirs from their own module with
 * `registerEdgeType(name, Component)`; import that module from your plugin
 * or command module so it is registered before the canvas first renders.
 * Edge components are used as given (not wrapped in React.memo).
 *
 * The map keeps one identity until a type is registered: React Flow warns
 * when `edgeTypes` changes between renders.
 */

import { useSyncExternalStore } from 'react'
import type { EdgeTypes } from '@xyflow/react'
import AttrEdge from './edges/AttrEdge'
import { KeyedStack } from './keyedStack'

/** A React Flow edge renderer. */
export type EdgeComponent = EdgeTypes[string]

// Per name a stack (keyedStack.ts, EA-12): removing an override brings back the one it replaced.
const registry = new KeyedStack<EdgeComponent>()
registry.push('attr', AttrEdge)

let current: EdgeTypes = Object.fromEntries(registry.entries())

const listeners = new Set<() => void>()
function notify() {
  for (const l of [...listeners]) l()
}

/** Add (or replace) a React Flow edge type. Returns a function that removes it again. */
export function registerEdgeType(name: string, Component: EdgeComponent): () => void {
  const remove = registry.push(name, Component)
  current = Object.fromEntries(registry.entries())
  notify()
  return () => {
    if (!remove()) return
    current = Object.fromEntries(registry.entries())
    notify()
  }
}

/** The edge type map (same object until a registration changes it). */
export function getEdgeTypes(): EdgeTypes {
  return current
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** The edge type map, re-rendering the caller when a type is registered. */
export function useEdgeTypes(): EdgeTypes {
  return useSyncExternalStore(subscribe, getEdgeTypes)
}
