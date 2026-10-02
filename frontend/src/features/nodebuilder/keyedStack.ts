/**
 * The override rule every node builder registry shares (W3 review fix
 * EA-12): registering a key that is already there overrides it, and
 * removing the override brings the earlier entry back.
 *
 * Used by node types (nodeTypes.ts), edge types (edgeTypes.ts), slots
 * (slots.ts), React Flow node sources (rfMapping.ts), canvas plugins
 * (canvasPlugins.ts, its own copy of the same rule) and Inspector sections.
 * Commands follow the same rule through their list (commands/index.ts:
 * newest wins, unregister reveals the older one).
 *
 * Without it, overriding a built-in (registerNodeType('ticker', X),
 * registerSlot('rightPanel', 'inspector', Y)) and then unregistering left no
 * entry at all for the rest of the module's life (a test file, an HMR
 * session).
 */

export class KeyedStack<T> {
  private stacks = new Map<string, T[]>()
  /** Keys in the order of their first (still live) registration. */
  private keys: string[] = []

  /** Add `value` under `key` (on top of an earlier one). Returns the remove function: true when it changed anything. */
  push(key: string, value: T): () => boolean {
    const stack = this.stacks.get(key)
    if (stack) stack.push(value)
    else {
      this.stacks.set(key, [value])
      this.keys.push(key)
    }
    let removed = false
    return () => {
      if (removed) return false
      const st = this.stacks.get(key)
      const i = st ? st.lastIndexOf(value) : -1
      if (!st || i < 0) return false
      removed = true
      st.splice(i, 1)
      if (st.length === 0) {
        this.stacks.delete(key)
        this.keys.splice(this.keys.indexOf(key), 1)
      }
      return true
    }
  }

  /** The live value of a key (the newest registration), or undefined. */
  get(key: string): T | undefined {
    return this.stacks.get(key)?.at(-1)
  }

  /** Every key with its live value, in first-registration order. */
  entries(): Array<[string, T]> {
    return this.keys.map(k => [k, this.stacks.get(k)!.at(-1)!])
  }

  /** Every live value, in first-registration order. */
  values(): T[] {
    return this.keys.map(k => this.stacks.get(k)!.at(-1)!)
  }
}
