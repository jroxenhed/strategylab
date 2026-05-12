/**
 * Lightweight SSE frame parser extracted from WalkForwardPanel so that the
 * malformed-event handling path can be unit-tested without mounting the full
 * panel component.
 */

/**
 * Parse one SSE frame's already-extracted `data:` lines into a typed value.
 *
 * Returns `null` (and emits a console.warn) when:
 * - `dataLines` is empty, OR
 * - the joined payload is not valid JSON.
 *
 * Never throws.
 */
export function parseSseFrame(dataLines: string[], label = 'SSE event'): unknown | null {
  if (dataLines.length === 0) return null
  try {
    return JSON.parse(dataLines.join('\n'))
  } catch (parseErr) {
    console.warn(`[WFA] Malformed ${label}, skipping:`, dataLines.join('\n'), parseErr)
    return null
  }
}
