import { useEffect, useRef, useState } from 'react'

/**
 * Track wall-clock time for a long-running request.
 *
 * `elapsed` ticks every ~250ms while `active=true`, reporting whole seconds
 * since `active` last flipped from false → true. On the falling edge,
 * captures `final` as the total seconds the run took, then keeps it stable
 * until the next run starts (at which point `final` resets to null and the
 * cycle repeats).
 *
 * Used by the strategy panels (WFA, Optimizer, Sensitivity) to render a
 * live `Running 12s…` counter during a request AND a `Completed in 18s`
 * indicator afterward. Uses `performance.now()` deltas so wall-clock stays
 * honest under browser-tab throttling.
 */
export function useRequestTimer(active: boolean): {
  elapsed: number
  final: number | null
} {
  const [elapsed, setElapsed] = useState(0)
  const [final, setFinal] = useState<number | null>(null)
  const startedAtRef = useRef<number | null>(null)

  useEffect(() => {
    if (!active) return

    startedAtRef.current = performance.now()
    setFinal(null)
    setElapsed(0)

    const id = window.setInterval(() => {
      const startedAt = startedAtRef.current
      if (startedAt === null) return
      setElapsed(Math.floor((performance.now() - startedAt) / 1000))
    }, 250)

    return () => {
      window.clearInterval(id)
      const startedAt = startedAtRef.current
      if (startedAt !== null) {
        setFinal(Math.round((performance.now() - startedAt) / 1000))
        startedAtRef.current = null
      }
    }
  }, [active])

  return { elapsed, final }
}
