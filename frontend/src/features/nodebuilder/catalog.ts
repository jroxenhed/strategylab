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
   * False for catalog-only nodes that render on canvas but whose compile
   * step is a no-op at T2 (currently "size" and "stop" output terminals).
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
export const SOURCE_OPTIONS = [
  'yahoo', 'alpaca', 'alpaca-iex', 'ibkr', 'polygon',
] as const;

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
    desc: "Relative Strength Index. Default period=14, type=sma.",
    reads: ["@close"],
    writes: ["@rsi"],
    defaults: {
      params: { period: 14, type: "sma" },
      ins: 1,
      outs: 1,
      subtitle: "RSI(14)",
    },
    compileActive: true,
    paramTypes: {
      period: { type: 'number' },
      type: { type: 'select', options: ['sma', 'ema', 'wma'] as const },
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
    desc: "Fraction of allocated capital deployed per trade (0–1). Default: 1.0 (100%).",
    reads: [],
    writes: ["@setting"],
    defaults: {
      params: { size: 1.0 },
      ins: 0,
      outs: 1,
      subtitle: "Size: 100%",
      setting_key: "position_size",
    },
    compileActive: true,
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
    desc: "(T4) Size terminal. Placeholder — compile ignores at T2. Wire a scalar for dynamic sizing.",
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
    desc: "(T4) Stop terminal. Placeholder — compile ignores at T2. Wire a scalar for dynamic stops.",
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
