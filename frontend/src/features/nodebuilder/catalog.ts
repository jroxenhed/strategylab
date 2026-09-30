/**
 * Node catalog for the frontend.
 *
 * The backend catalog (backend/nodebuilder/nodes.py) is the one source of
 * truth (plan decision D9). backend/scripts/export_nodebuilder_catalog.py
 * writes it out as catalog.generated.ts, and this file builds NODE_CATALOG
 * from that. Only UI extras live here: how a unit is shown on a node row, the
 * older `paramTypes` shape that ParamRow reads, and the port helpers. Names,
 * descriptions, params, defaults, options and ports all come from the backend,
 * so the two catalogs cannot drift.
 *
 * Pure data and helpers. No runtime logic, no React imports.
 */

import {
  GENERATED_CATALOG,
  type GeneratedCatalogEntry,
  type ParamSpec,
  type PortsSpec,
} from './catalog.generated';

export {
  INTERVAL_OPTIONS,
  SOURCE_OPTIONS,
  RSI_TYPE_OPTIONS,
  RSI_DEFAULT_TYPE,
  TRAILING_STOP_TYPE_OPTIONS,
  TRAILING_STOP_SOURCE_OPTIONS,
  TRAILING_STOP_DEFAULTS,
} from './catalog.generated';
export type {
  ParamSpec,
  ParamType,
  ParamUnit,
  ParamDtype,
  PortSpec,
  PortsSpec,
} from './catalog.generated';

/**
 * Per-param input type for the inline editor (ParamRow), so enum-style params
 * render as `<select>` instead of free-text inputs. Built from the catalog
 * ParamSpec. If a key is missing, ParamRow infers the type from `typeof value`.
 */
export interface ParamTypeSpec {
  type: 'number' | 'string' | 'select';
  /** Required when type === 'select'. */
  options?: readonly string[];
  /**
   * Short unit label shown next to the input, e.g. "%", "bps", "fraction".
   * It says how the stored value is read, so the edit field and the
   * read-only chip can't be taken to mean different things.
   */
  unit?: string;
}

export interface NodeCatalogEntry {
  /** Unique node-type identifier, e.g. "rsi", "crosses_below". */
  name: string;
  /** Category key — must be a key of CATS from categories.ts. */
  cat: string;
  /** Short human-readable description shown in Tab-menu search. */
  desc: string;
  /**
   * Stream attributes this node reads.
   * Empty array for source nodes (ticker) and Settings constants.
   */
  reads: readonly string[];
  /**
   * Stream attributes this node writes.
   * Empty array for terminal nodes (entry, exit).
   */
  writes: readonly string[];
  /**
   * Node-instance defaults:
   *   params      — param defaults, one per ParamSpec (may be empty).
   *   ins         — expected inbound wire count.
   *   outs        — expected outbound wire count.
   *   subtitle    — optional subtitle rendered in the node body.
   *   setting_key — (Settings nodes only) simulator field key for Unit 7a.
   */
  defaults: {
    params: Record<string, unknown>;
    ins: number;
    outs: number;
    subtitle: string | null;
    setting_key?: string;
  };
  /**
   * False for catalog-only nodes that render on canvas but that the backtest
   * cannot run yet (currently "size" and "stop" output terminals). Compile
   * ignores an unwired one and refuses (400) a wired one. The Tab menu hides
   * these so users can't place a node that does nothing. The read-only
   * viewer still renders them if a graph contains one.
   */
  compileActive: boolean;
  /** Per-param input type overrides — drives ParamRow rendering. */
  paramTypes?: Record<string, ParamTypeSpec>;
  /**
   * The full param specs from the backend (plan section 4.3), in display
   * order. Always set on NODE_CATALOG entries; optional only so small
   * hand-built test entries stay valid.
   */
  params?: readonly ParamSpec[];
  /** The input ports from the backend (section 4.3). Always set on NODE_CATALOG entries. */
  inputs?: PortsSpec;
}

// ---------------------------------------------------------------------------
// UI extras: how units show on a node row
// ---------------------------------------------------------------------------

// The catalog says "frac"; the node row says "fraction" and adds the percent
// (see nodes/paramFormat.ts unitLabel).
const UNIT_DISPLAY: Record<string, string> = { frac: 'fraction' };

// Lookback periods are always in bars, so the node row leaves "bars" off to
// stay short. The full unit is still in ParamSpec.unit for the Inspector.
const UNITS_HIDDEN_ON_ROW: ReadonlySet<string> = new Set(['bars']);

/** The ParamRow type for one ParamSpec. */
function toParamTypeSpec(spec: ParamSpec): ParamTypeSpec {
  let out: ParamTypeSpec;
  if (spec.type === 'number' || spec.type === 'int') {
    out = { type: 'number' };
  } else if (spec.type === 'select') {
    out = { type: 'select', options: spec.options ?? [] };
  } else if (spec.type === 'bool') {
    // A select stores the text "true"/"false"; the backend reads both.
    out = { type: 'select', options: ['false', 'true'] };
  } else {
    out = { type: 'string' };
  }
  if (spec.unit && !UNITS_HIDDEN_ON_ROW.has(spec.unit)) {
    out.unit = UNIT_DISPLAY[spec.unit] ?? spec.unit;
  }
  return out;
}

/** One frontend catalog entry from its generated backend entry. */
function toEntry(g: GeneratedCatalogEntry): NodeCatalogEntry {
  const defaults: NodeCatalogEntry['defaults'] = {
    params: Object.fromEntries(g.params.map((p) => [p.name, p.default])),
    ins: g.ins,
    outs: g.outs,
    subtitle: g.subtitle,
  };
  if (g.setting_key !== null) defaults.setting_key = g.setting_key;
  const entry: NodeCatalogEntry = {
    name: g.name,
    cat: g.cat,
    desc: g.desc,
    reads: g.reads,
    writes: g.writes,
    defaults,
    compileActive: g.compile_active,
    params: g.params,
    inputs: g.inputs,
  };
  if (g.params.length > 0) {
    entry.paramTypes = Object.fromEntries(g.params.map((p) => [p.name, toParamTypeSpec(p)]));
  }
  return entry;
}

export const NODE_CATALOG: readonly NodeCatalogEntry[] = GENERATED_CATALOG.map(toEntry);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const _index = new Map<string, NodeCatalogEntry>(
  NODE_CATALOG.map((e) => [e.name, e])
);

/** Return the catalog entry for `name`, or throw if missing. */
export function getNode(name: string): NodeCatalogEntry {
  const entry = _index.get(name);
  if (!entry) {
    throw new Error(`No node named "${name}" in NODE_CATALOG.`);
  }
  return entry;
}

/** Return NODE_CATALOG entries grouped by category, insertion order preserved. */
export function catalogByCategory(): Record<string, NodeCatalogEntry[]> {
  const result: Record<string, NodeCatalogEntry[]> = {};
  for (const entry of NODE_CATALOG) {
    if (!result[entry.cat]) {
      result[entry.cat] = [];
    }
    result[entry.cat].push(entry);
  }
  return result;
}

// ---------------------------------------------------------------------------
// Ports: which node types can take a wire in, or send one out
// ---------------------------------------------------------------------------

// A Ticker is a source and a Settings node sets a value, so neither takes a
// wire in (their PortsSpec has max 0).  Entry/Exit/Size/Stop are terminals
// and Settings nodes feed nothing, so neither sends a wire out.  compile
// refuses these wires, and the canvas draws no handle for them.
const NO_OUTPUT_CATS: ReadonlySet<string> = new Set(['output', 'settings']);

/** True when a node of this type has an input port.  Unknown types do. */
export function hasInputPort(nodeType: string | undefined): boolean {
  const entry = nodeType ? _index.get(nodeType) : undefined;
  return !entry || (entry.inputs?.max ?? 1) > 0;
}

/** True when a node of this type has an output port.  Unknown types do. */
export function hasOutputPort(nodeType: string | undefined): boolean {
  const entry = nodeType ? _index.get(nodeType) : undefined;
  return !entry || !NO_OUTPUT_CATS.has(entry.cat);
}

/**
 * True when a wire may go from a node of `fromType` to one of `toType`.
 * A wire with no port at either end can't be drawn, so it would sit in the
 * graph unseen, unselectable, and still count in cycle checks.
 */
export function canWire(fromType: string | undefined, toType: string | undefined): boolean {
  return hasOutputPort(fromType) && hasInputPort(toType);
}
