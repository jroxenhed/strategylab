/**
 * Which nodes draw the unsupported-node card (spec S13).
 *
 * A node the compiler cannot run is shown honestly and kept inert:
 * - its type is missing from the catalog (an old file, a type removed, an
 *   auto-rendered rule the graph cannot draw);
 * - its catalog entry is not compile-active and something is wired into it
 *   (an unwired Size or Stop terminal is ignored by compile, so it keeps
 *   its normal card; the S07 banner follows the same rule);
 * - the server says so (`unsupported_node`, `unknown_node_type`), for
 *   example a per-direction Settings node.
 */

import type { Diagnostic } from '../../../api/nodebuilderValidate'
import { catalogEntry } from '../streamLabels'

/** Diagnostic codes that mark a node unsupported. */
export const UNSUPPORTED_CODES: ReadonlySet<string> = new Set(['unsupported_node', 'unknown_node_type'])

/** True when the node should draw the S13 card. */
export function isUnsupportedNode(
  nodeType: string | undefined,
  wiredIn: boolean,
  diagnostics: readonly Diagnostic[],
): boolean {
  if (!nodeType) return false
  const entry = catalogEntry(nodeType)
  if (!entry) return true
  if (!entry.compileActive && wiredIn) return true
  return diagnostics.some(d => UNSUPPORTED_CODES.has(d.code))
}

/** The badge message to show before /validate has answered. */
export function localUnsupportedDiagnostic(nodeId: string, nodeType: string): Diagnostic {
  return {
    node_id: nodeId,
    path: null,
    severity: 'error',
    code: catalogEntry(nodeType) ? 'unsupported_node' : 'unknown_node_type',
    message: `Unsupported in graphs: ${nodeType}`,
    param: null,
    port: null,
    line: null,
    col: null,
    end_line: null,
    end_col: null,
  }
}

/** A stored param value as plain text for the card's read-only rows. */
export function formatStoredParam(value: unknown): string {
  if (value === null || value === undefined) return 'none'
  if (Array.isArray(value)) return value.map(v => String(v)).join(', ')
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}
