/**
 * Layout slots of the node builder (W3 pre-step 3.0).
 *
 * NodeBuilder draws a fixed frame with named places; other items fill them
 * with `registerSlot(name, id, Component, order)` from their own module,
 * without editing NodeBuilder.tsx:
 *
 *   toolbarLeft   left of the graph toolbar
 *   toolbarRight  right of the graph toolbar
 *   rightPanel    right of the canvas column, full height (the Inspector)
 *   bottomPanel   under the canvas, inside the canvas column (the Data Sheet, W4)
 *   statusBar     full width at the bottom (S20)
 *   overlays      on top of everything (the `?` overlay, context menus)
 *   dialogs       modal dialogs
 *
 * A slot component takes no props. It reads the store, and `useBuilder()`
 * for the graph session and the Run and Stop actions. Lower `order` comes
 * first; ties keep registration order. Register from a module that loads
 * automatically (a plugins/ or commands/ module), so the slot is filled
 * before the builder first renders.
 */

import {
  Component,
  createContext,
  createElement,
  Fragment,
  memo,
  useContext,
  useSyncExternalStore,
  type ComponentType,
  type CSSProperties,
  type ErrorInfo,
  type ReactElement,
  type ReactNode,
} from 'react'
import type { GraphSession } from './useGraphSession'
import { KeyedStack } from './keyedStack'

export type SlotName = 'toolbarLeft' | 'toolbarRight' | 'rightPanel' | 'bottomPanel' | 'statusBar' | 'overlays' | 'dialogs'

export const SLOT_NAMES: readonly SlotName[] = [
  'toolbarLeft', 'toolbarRight', 'rightPanel', 'bottomPanel', 'statusBar', 'overlays', 'dialogs',
]

export interface SlotEntry {
  id: string
  Component: ComponentType
  order: number
}

/** Default order for entries registered without one. */
export const DEFAULT_SLOT_ORDER = 100

// Per slot, a stack per entry id (keyedStack.ts, EA-12): replacing an
// entry by id and then removing the replacement brings the old one back.
const stacks = Object.fromEntries(SLOT_NAMES.map(n => [n, new KeyedStack<SlotEntry>()])) as Record<SlotName, KeyedStack<SlotEntry>>
const slots = Object.fromEntries(SLOT_NAMES.map(n => [n, [] as SlotEntry[]])) as Record<SlotName, SlotEntry[]>

function rebuildSlot(name: SlotName): void {
  // A stable sort keeps first-registration order for equal `order` values.
  slots[name] = stacks[name].values().sort((a, b) => a.order - b.order)
}
const listeners = new Set<() => void>()

/**
 * Put a component in a slot (an entry with the same id in that slot is
 * replaced). Returns a function that removes it again.
 */
export function registerSlot(name: SlotName, id: string, Component: ComponentType, order = DEFAULT_SLOT_ORDER): () => void {
  const entry: SlotEntry = { id, Component, order }
  const remove = stacks[name].push(id, entry)
  rebuildSlot(name)
  for (const l of [...listeners]) l()
  return () => {
    if (!remove()) return
    rebuildSlot(name)
    for (const l of [...listeners]) l()
  }
}

/** The entries of a slot, in order. */
export function listSlot(name: SlotName): readonly SlotEntry[] {
  return slots[name]
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** The entries of a slot, re-rendering the caller when they change. */
export function useSlot(name: SlotName): readonly SlotEntry[] {
  return useSyncExternalStore(subscribe, () => slots[name])
}

// ---------------------------------------------------------------------------
// What slot components can reach in NodeBuilder
// ---------------------------------------------------------------------------

export interface BuilderApi {
  /** Save, Open, Rename and the other graph file actions (S01). */
  session: GraphSession
  /** Run the backtest cook (the same as Cmd+Enter). */
  runBacktest(): void
  /** Stop the backtest cook in flight. */
  stopBacktest(): void
  /** Open the graph-wide diagnostics list (S05) under an element. */
  openDiagnostics(anchor: HTMLElement): void
}

export const BuilderContext = createContext<BuilderApi | null>(null)

/** NodeBuilder's actions, or null outside a builder (a panel rendered alone in a test). */
export function useBuilder(): BuilderApi | null {
  return useContext(BuilderContext)
}

// ---------------------------------------------------------------------------
// <Slot>: draws a slot's entries
// ---------------------------------------------------------------------------

/** Keeps one broken slot component from blanking the whole builder. */
class SlotBoundary extends Component<{ id: string; children?: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(`nodebuilder: slot "${this.props.id}" failed`, error, info.componentStack)
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}

/**
 * One slot entry, memoized (IP-1). Slot components take no props, so the
 * host re-rendering (NodeBuilder on every commit) never needs to reach
 * them: they re-render from their own store subscriptions only.
 */
const SlotItem = memo(function SlotItem({ C }: { C: ComponentType }) {
  return createElement(C)
})

export interface SlotProps {
  name: SlotName
  /** Only entries with order >= minOrder (to split a slot around other content). */
  minOrder?: number
  /** Only entries with order <= maxOrder. */
  maxOrder?: number
  /** With a className or style, the entries are wrapped in a div that has them. */
  className?: string
  style?: CSSProperties
}

/** Draw a slot's entries in order, or nothing when it is empty. */
export function Slot({ name, minOrder, maxOrder, className, style }: SlotProps): ReactElement | null {
  const all = useSlot(name)
  const entries = all.filter(e => (minOrder == null || e.order >= minOrder) && (maxOrder == null || e.order <= maxOrder))
  if (entries.length === 0) return null
  const children = entries.map(e =>
    createElement(SlotBoundary, { key: e.id, id: `${name}/${e.id}` }, createElement(SlotItem, { C: e.Component })),
  )
  if (className == null && style == null) return createElement(Fragment, null, children)
  return createElement('div', { className, style, 'data-nb-slot': name }, children)
}
