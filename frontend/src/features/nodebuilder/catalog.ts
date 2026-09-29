/**
 * Core 14 node catalog — TypeScript mirror of backend/nodebuilder/nodes.py.
 *
 * Unit 2: static metadata only. No runtime logic, no React imports.
 * Implementations land in Unit 7b on the Python side; this file stays pure data.
 */

/**
 * Optional per-param schema override. Used by the inline editor (ParamRow) so
 * enum-style params render as `<select>` instead of free-text inputs.
 * If a key is missing here, ParamRow falls back to inferring from `typeof value`.
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
   *   params      — indicator / comparison param defaults (may be empty).
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
}

// ---------------------------------------------------------------------------
// Shared option lists — central so adding a provider / interval updates every
// node that exposes it (currently only ticker, but more could follow).
// ---------------------------------------------------------------------------
export const INTERVAL_OPTIONS = [
  '1m', '5m', '15m', '30m', '1h', '1d', '1wk', '1mo',
] as const;
// Must match the providers registered in backend/shared.py.
export const SOURCE_OPTIONS = [
  'yahoo', 'alpaca', 'alpaca-iex', 'ibkr',
] as const;
// Must match the smoothing types backend/indicators.py compute_rsi accepts.
// Any other value silently falls back to sma there, so offer only these two.
export const RSI_TYPE_OPTIONS = ['sma', 'wilder'] as const;
// Same default as the rule builder and the chart's RSI (shared/types/indicators.ts).
export const RSI_DEFAULT_TYPE = 'wilder';
// Trailing stop choices and defaults. Must match backend nodes.py
// TRAILING_STOP_* (models.TrailingStopConfig): type pct or atr, and the price
// (high or close) that moves the peak.
export const TRAILING_STOP_TYPE_OPTIONS = ['pct', 'atr'] as const;
export const TRAILING_STOP_SOURCE_OPTIONS = ['high', 'close'] as const;
export const TRAILING_STOP_DEFAULTS = {
  type: 'pct',
  value: 5.0,
  source: 'high',
  activate_on_profit: false,
  activate_pct: 0.0,
} as const;

export const NODE_CATALOG: readonly NodeCatalogEntry[] = [
  // ── Ticker (source) ────────────────────────────────────────────────────
  {
    name: "ticker",
    cat: "ticker",
    desc: "Market data source: OHLCV price series for a symbol.",
    reads: [],
    writes: ["@open", "@high", "@low", "@close", "@volume"],
    defaults: {
      params: { symbol: "AAPL", interval: "1d", source: "yahoo" },
      ins: 0,
      outs: 5,
      subtitle: null,
    },
    compileActive: true,
    paramTypes: {
      symbol: { type: 'string' },
      interval: { type: 'select', options: INTERVAL_OPTIONS },
      source: { type: 'select', options: SOURCE_OPTIONS },
    },
  },

  // ── Indicators ─────────────────────────────────────────────────────────
  {
    name: "rsi",
    cat: "indicator",
    desc: "Relative Strength Index. Default period=14, type=wilder (sma also available).",
    reads: ["@close"],
    writes: ["@rsi"],
    defaults: {
      params: { period: 14, type: RSI_DEFAULT_TYPE },
      ins: 1,
      outs: 1,
      subtitle: "RSI(14)",
    },
    compileActive: true,
    paramTypes: {
      period: { type: 'number' },
      type: { type: 'select', options: RSI_TYPE_OPTIONS },
    },
  },
  {
    name: "macd",
    cat: "indicator",
    desc: "MACD: line, signal, and histogram series. Defaults: fast=12, slow=26, signal=9.",
    reads: ["@close"],
    writes: ["@macd_line", "@macd_signal", "@macd_histogram"],
    defaults: {
      params: { fast: 12, slow: 26, signal: 9 },
      ins: 1,
      outs: 3,
      subtitle: "MACD(12,26,9)",
    },
    compileActive: true,
    paramTypes: {
      fast: { type: 'number' },
      slow: { type: 'number' },
      signal: { type: 'number' },
    },
  },
  {
    name: "sma",
    cat: "indicator",
    desc: "Simple Moving Average. Default period=20.",
    reads: ["@close"],
    writes: ["@sma"],
    defaults: {
      params: { period: 20 },
      ins: 1,
      outs: 1,
      subtitle: "SMA(20)",
    },
    compileActive: true,
    paramTypes: { period: { type: 'number' } },
  },
  {
    name: "ema",
    cat: "indicator",
    desc: "Exponential Moving Average. Default period=20.",
    reads: ["@close"],
    writes: ["@ema"],
    defaults: {
      params: { period: 20 },
      ins: 1,
      outs: 1,
      subtitle: "EMA(20)",
    },
    compileActive: true,
    paramTypes: { period: { type: 'number' } },
  },
  {
    name: "bollinger",
    cat: "indicator",
    desc: "Bollinger Bands: upper, middle, lower. Default period=20, stddev=2.",
    reads: ["@close"],
    writes: ["@bb_upper", "@bb_middle", "@bb_lower"],
    defaults: {
      params: { period: 20, stddev: 2.0 },
      ins: 1,
      outs: 3,
      subtitle: "BB(20,2)",
    },
    compileActive: true,
    paramTypes: {
      period: { type: 'number' },
      stddev: { type: 'number' },
    },
  },
  {
    name: "atr",
    cat: "indicator",
    desc: "Average True Range. Default period=14.",
    reads: ["@high", "@low", "@close"],
    writes: ["@atr"],
    defaults: {
      params: { period: 14 },
      ins: 3,
      outs: 1,
      subtitle: "ATR(14)",
    },
    compileActive: true,
    paramTypes: { period: { type: 'number' } },
  },

  // ── Comparisons ────────────────────────────────────────────────────────
  {
    name: "crosses_above",
    cat: "comparison",
    desc: "True on the bar where the left series crosses above the right series.",
    reads: ["@series"],
    writes: ["@bool"],
    defaults: {
      params: { threshold: null },
      ins: 2,
      outs: 1,
      subtitle: "crosses above",
    },
    compileActive: true,
    paramTypes: { threshold: { type: 'number' } },
  },
  {
    name: "crosses_below",
    cat: "comparison",
    desc: "True on the bar where the left series crosses below the right series.",
    reads: ["@series"],
    writes: ["@bool"],
    defaults: {
      params: { threshold: null },
      ins: 2,
      outs: 1,
      subtitle: "crosses below",
    },
    compileActive: true,
    paramTypes: { threshold: { type: 'number' } },
  },
  {
    name: "above",
    cat: "comparison",
    desc: "True when the left series is above the right series (or a scalar threshold).",
    reads: ["@series"],
    writes: ["@bool"],
    defaults: {
      params: { threshold: null },
      ins: 2,
      outs: 1,
      subtitle: "above",
    },
    compileActive: true,
    paramTypes: { threshold: { type: 'number' } },
  },
  {
    name: "below",
    cat: "comparison",
    desc: "True when the left series is below the right series (or a scalar threshold).",
    reads: ["@series"],
    writes: ["@bool"],
    defaults: {
      params: { threshold: null },
      ins: 2,
      outs: 1,
      subtitle: "below",
    },
    compileActive: true,
    paramTypes: { threshold: { type: 'number' } },
  },

  // ── Logic ──────────────────────────────────────────────────────────────
  {
    name: "and",
    cat: "logic",
    desc: "True when ALL incoming boolean signals are true.",
    reads: ["@bool"],
    writes: ["@bool"],
    defaults: {
      params: {},
      ins: 2,
      outs: 1,
      subtitle: "AND",
    },
    compileActive: true,
  },
  {
    name: "or",
    cat: "logic",
    desc: "True when ANY incoming boolean signal is true.",
    reads: ["@bool"],
    writes: ["@bool"],
    defaults: {
      params: {},
      ins: 2,
      outs: 1,
      subtitle: "OR",
    },
    compileActive: true,
  },
  {
    name: "not",
    cat: "logic",
    desc: "Inverts the incoming boolean signal.",
    reads: ["@bool"],
    writes: ["@bool"],
    defaults: {
      params: {},
      ins: 1,
      outs: 1,
      subtitle: "NOT",
    },
    compileActive: true,
  },

  // ── Settings ───────────────────────────────────────────────────────────
  {
    name: "position_size",
    cat: "settings",
    desc: "Fraction of allocated capital used per trade, from 0 to 1 (1 = 100%, 0.5 = 50%). Default: 1.",
    reads: [],
    writes: ["@setting"],
    defaults: {
      params: { size: 1.0 },
      ins: 0,
      outs: 1,
      subtitle: "Size: 1 (100%)",
      setting_key: "position_size",
    },
    compileActive: true,
    // Backend reads size as a fraction in (0, 1] (nodes.py position_size_impl).
    paramTypes: { size: { type: 'number', unit: 'fraction' } },
  },
  {
    name: "stop_loss",
    cat: "settings",
    desc: "Fixed stop-loss as a percentage below/above entry. Default: 5.0%.",
    reads: [],
    writes: ["@setting"],
    defaults: {
      params: { pct: 5.0 },
      ins: 0,
      outs: 1,
      subtitle: "Stop: 5%",
      setting_key: "stop_loss",
    },
    compileActive: true,
    paramTypes: { pct: { type: 'number', unit: '%' } },
  },
  {
    name: "slippage",
    cat: "settings",
    desc: "Modeled slippage cost per leg in basis points. Default: 2.0 bps.",
    reads: [],
    writes: ["@setting"],
    defaults: {
      params: { bps: 2.0 },
      ins: 0,
      outs: 1,
      subtitle: "Slippage: 2 bps",
      setting_key: "slippage_bps",
    },
    compileActive: true,
    paramTypes: { bps: { type: 'number', unit: 'bps' } },
  },
  {
    name: "commission",
    cat: "settings",
    desc: "Per-share commission rate and minimum per order. Defaults match Alpaca (free).",
    reads: [],
    writes: ["@setting"],
    defaults: {
      params: { per_share_rate: 0.0, min_per_order: 0.0 },
      ins: 0,
      outs: 1,
      subtitle: "Commission: free",
      setting_key: "commission",
    },
    compileActive: true,
    paramTypes: {
      per_share_rate: { type: 'number', unit: '$/share' },
      min_per_order: { type: 'number', unit: '$' },
    },
  },
  // Same five fields as backend models.TrailingStopConfig, so a rule
  // strategy's trailing stop renders as this node and runs the same.
  {
    name: "trailing_stop",
    cat: "settings",
    desc: "Trailing stop. type=pct trails value % from the peak; type=atr trails value x ATR(14). Optionally waits until the trade is activate_pct % in profit.",
    reads: [],
    writes: ["@setting"],
    defaults: {
      params: { ...TRAILING_STOP_DEFAULTS },
      ins: 0,
      outs: 1,
      subtitle: "Trail: 5%",
      setting_key: "trailing_stop",
    },
    compileActive: true,
    paramTypes: {
      type: { type: 'select', options: TRAILING_STOP_TYPE_OPTIONS },
      // A percent when type=pct, a multiple of ATR when type=atr.
      value: { type: 'number', unit: '% or x ATR' },
      source: { type: 'select', options: TRAILING_STOP_SOURCE_OPTIONS },
      // A select stores the text "true"/"false"; the backend reads both.
      activate_on_profit: { type: 'select', options: ['false', 'true'] },
      activate_pct: { type: 'number', unit: '%' },
    },
  },

  // ── Output terminals — compile-active ──────────────────────────────────
  {
    name: "entry",
    cat: "output",
    desc: "Entry terminal. Wire the buy-signal boolean here to trigger long entries.",
    reads: ["@bool"],
    writes: [],
    defaults: {
      params: {},
      ins: 1,
      outs: 0,
      subtitle: "Entry",
    },
    compileActive: true,
  },
  {
    name: "exit",
    cat: "output",
    desc: "Exit terminal. Wire the sell-signal boolean here to trigger exits.",
    reads: ["@bool"],
    writes: [],
    defaults: {
      params: {},
      ins: 1,
      outs: 0,
      subtitle: "Exit",
    },
    compileActive: true,
  },

  // ── Output terminals — catalog-only at T2 ─────────────────────────────
  {
    name: "size",
    cat: "output",
    desc: "(T4) Size terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
    reads: ["@bool"],
    writes: [],
    defaults: {
      params: {},
      ins: 1,
      outs: 0,
      subtitle: "Size (T4)",
    },
    compileActive: false,
  },
  {
    name: "stop",
    cat: "output",
    desc: "(T4) Stop terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
    reads: ["@bool"],
    writes: [],
    defaults: {
      params: {},
      ins: 1,
      outs: 0,
      subtitle: "Stop (T4)",
    },
    compileActive: false,
  },
] as const;

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
// wire in.  Entry/Exit/Size/Stop are terminals and Settings nodes feed
// nothing, so neither sends a wire out.  compile refuses these wires, and
// the canvas draws no handle for them.
const NO_INPUT_CATS: ReadonlySet<string> = new Set(['ticker', 'settings']);
const NO_OUTPUT_CATS: ReadonlySet<string> = new Set(['output', 'settings']);

/** True when a node of this type has an input port.  Unknown types do. */
export function hasInputPort(nodeType: string | undefined): boolean {
  const entry = nodeType ? _index.get(nodeType) : undefined;
  return !entry || !NO_INPUT_CATS.has(entry.cat);
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
