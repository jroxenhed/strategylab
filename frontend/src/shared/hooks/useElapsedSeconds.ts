import { useEffect, useState } from 'react'

/**
 * Tick once per second while `active` is true. Returns elapsed whole seconds
 * since `active` last flipped from false → true. Resets to 0 when `active`
 * flips back to false.
 *
 * Used by the strategy panels (WFA, Optimizer, Sensitivity) to render a live
 * `Running 12s` counter while a long backend request is in flight. The pre-
 * flight estimate is shown beforehand; this hook covers the in-flight gap so
 * the user has visible feedback instead of staring at a frozen button.
 */
export function useElapsedSeconds(active: boolean): number {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!active) {
      setElapsed(0)
      return
    }
    const startedAt = performance.now()
    const id = window.setInterval(() => {
      setElapsed(Math.floor((performance.now() - startedAt) / 1000))
    }, 250)
    return () => window.clearInterval(id)
  }, [active])
  return elapsed
}
