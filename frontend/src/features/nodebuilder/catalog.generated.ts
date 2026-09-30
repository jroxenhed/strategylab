// GENERATED FILE. Do not edit by hand.
//
// Source: backend/nodebuilder/nodes.py (the node catalog, plan decision D9).
// Regenerate from the repo root with:
//   backend/venv/bin/python backend/scripts/export_nodebuilder_catalog.py
// backend/tests/nodebuilder/test_catalog_consistency.py fails when this file
// no longer matches the backend catalog.
//
// UI-only extras (unit display text, port helpers) live in catalog.ts.

export type ParamType = "number" | "int" | "string" | "select" | "bool" | "attr" | "attr_list" | "write" | "path" | "time_range";
export type ParamUnit = "%" | "bps" | "bars" | "frac" | "$" | "$/share" | "% or x ATR";
export type ParamDtype = "float" | "bool" | "any";

/** One param of a node type (plan section 4.3). */
export interface ParamSpec {
  name: string;
  type: ParamType;
  label: string;
  default: unknown;
  min?: number;
  max?: number;
  step?: number;
  unit?: ParamUnit | null;
  options?: readonly string[];
  dtype?: ParamDtype;
  optional?: boolean;
  /** False for params that may never hold code (Ticker symbol and interval). */
  code_able?: boolean;
}

/** One input port. Its id is in<k> by position. */
export interface PortSpec {
  label: string;
  optional?: boolean;
}

/** The input ports of a node type (plan section 4.3). */
export interface PortsSpec {
  ports: readonly PortSpec[];
  dynamic: boolean;
  min: number;
  max: number;
}

/** A backend catalog entry, as nodes.NodeCatalogEntry.to_json() gives it. */
export interface GeneratedCatalogEntry {
  name: string;
  cat: string;
  desc: string;
  compile_active: boolean;
  inputs: PortsSpec;
  params: readonly ParamSpec[];
  reads: readonly string[];
  writes: readonly string[];
  subtitle: string | null;
  setting_key: string | null;
  ins: number;
  outs: number;
}

export const INTERVAL_OPTIONS = ["1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo"] as const;
export const SOURCE_OPTIONS = ["yahoo", "alpaca", "alpaca-iex", "ibkr"] as const;
export const RSI_TYPE_OPTIONS = ["sma", "wilder"] as const;
export const RSI_DEFAULT_TYPE = "wilder";
export const TRAILING_STOP_TYPE_OPTIONS = ["pct", "atr"] as const;
export const TRAILING_STOP_SOURCE_OPTIONS = ["high", "close"] as const;
export const TRAILING_STOP_DEFAULTS = { "type": "pct", "value": 5.0, "source": "high", "activate_on_profit": false, "activate_pct": 0.0 } as const;

export const GENERATED_CATALOG: readonly GeneratedCatalogEntry[] = [
  {
    "name": "ticker",
    "cat": "ticker",
    "desc": "Market data source: OHLCV price series for a symbol.",
    "compile_active": true,
    "inputs": {
      "ports": [],
      "dynamic": false,
      "min": 0,
      "max": 0
    },
    "params": [
      {
        "name": "symbol",
        "type": "string",
        "label": "symbol",
        "default": "AAPL",
        "code_able": false
      },
      {
        "name": "interval",
        "type": "select",
        "label": "interval",
        "default": "1d",
        "options": ["1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo"],
        "code_able": false
      },
      {
        "name": "source",
        "type": "select",
        "label": "source",
        "default": "yahoo",
        "options": ["yahoo", "alpaca", "alpaca-iex", "ibkr"]
      }
    ],
    "reads": [],
    "writes": ["@open", "@high", "@low", "@close", "@volume"],
    "subtitle": null,
    "setting_key": null,
    "ins": 0,
    "outs": 5
  },
  {
    "name": "rsi",
    "cat": "indicator",
    "desc": "Relative Strength Index. Default period=14, type=wilder (sma also available).",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "period",
        "type": "int",
        "label": "period",
        "default": 14,
        "min": 2,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "type",
        "type": "select",
        "label": "type",
        "default": "wilder",
        "options": ["sma", "wilder"]
      }
    ],
    "reads": ["@close"],
    "writes": ["@rsi"],
    "subtitle": "RSI(14)",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "macd",
    "cat": "indicator",
    "desc": "MACD: line, signal, and histogram series. Defaults: fast=12, slow=26, signal=9.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "fast",
        "type": "int",
        "label": "fast",
        "default": 12,
        "min": 2,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "slow",
        "type": "int",
        "label": "slow",
        "default": 26,
        "min": 2,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "signal",
        "type": "int",
        "label": "signal",
        "default": 9,
        "min": 2,
        "max": 500,
        "unit": "bars"
      }
    ],
    "reads": ["@close"],
    "writes": ["@macd_line", "@macd_signal", "@macd_histogram"],
    "subtitle": "MACD(12,26,9)",
    "setting_key": null,
    "ins": 1,
    "outs": 3
  },
  {
    "name": "sma",
    "cat": "indicator",
    "desc": "Simple Moving Average. Default period=20.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "period",
        "type": "int",
        "label": "period",
        "default": 20,
        "min": 2,
        "max": 500,
        "unit": "bars"
      }
    ],
    "reads": ["@close"],
    "writes": ["@sma"],
    "subtitle": "SMA(20)",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "ema",
    "cat": "indicator",
    "desc": "Exponential Moving Average. Default period=20.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "period",
        "type": "int",
        "label": "period",
        "default": 20,
        "min": 2,
        "max": 500,
        "unit": "bars"
      }
    ],
    "reads": ["@close"],
    "writes": ["@ema"],
    "subtitle": "EMA(20)",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "bollinger",
    "cat": "indicator",
    "desc": "Bollinger Bands: upper, middle, lower. Default period=20, stddev=2.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "period",
        "type": "int",
        "label": "period",
        "default": 20,
        "min": 2,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "stddev",
        "type": "number",
        "label": "stddev",
        "default": 2.0,
        "min": 0.5,
        "max": 5.0
      }
    ],
    "reads": ["@close"],
    "writes": ["@bb_upper", "@bb_middle", "@bb_lower"],
    "subtitle": "BB(20,2)",
    "setting_key": null,
    "ins": 1,
    "outs": 3
  },
  {
    "name": "atr",
    "cat": "indicator",
    "desc": "Average True Range. Default period=14.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "period",
        "type": "int",
        "label": "period",
        "default": 14,
        "min": 2,
        "max": 500,
        "unit": "bars"
      }
    ],
    "reads": ["@high", "@low", "@close"],
    "writes": ["@atr"],
    "subtitle": "ATR(14)",
    "setting_key": null,
    "ins": 3,
    "outs": 1
  },
  {
    "name": "crosses_above",
    "cat": "comparison",
    "desc": "True on the bar where the left series crosses above the right series.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" },
        { "label": "b", "optional": true }
      ],
      "dynamic": false,
      "min": 1,
      "max": 2
    },
    "params": [
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      }
    ],
    "reads": ["@series"],
    "writes": ["@bool"],
    "subtitle": "crosses above",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "crosses_below",
    "cat": "comparison",
    "desc": "True on the bar where the left series crosses below the right series.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" },
        { "label": "b", "optional": true }
      ],
      "dynamic": false,
      "min": 1,
      "max": 2
    },
    "params": [
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      }
    ],
    "reads": ["@series"],
    "writes": ["@bool"],
    "subtitle": "crosses below",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "above",
    "cat": "comparison",
    "desc": "True when the left series is above the right series (or a scalar threshold).",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" },
        { "label": "b", "optional": true }
      ],
      "dynamic": false,
      "min": 1,
      "max": 2
    },
    "params": [
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      }
    ],
    "reads": ["@series"],
    "writes": ["@bool"],
    "subtitle": "above",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "below",
    "cat": "comparison",
    "desc": "True when the left series is below the right series (or a scalar threshold).",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" },
        { "label": "b", "optional": true }
      ],
      "dynamic": false,
      "min": 1,
      "max": 2
    },
    "params": [
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      }
    ],
    "reads": ["@series"],
    "writes": ["@bool"],
    "subtitle": "below",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "and",
    "cat": "logic",
    "desc": "True when ALL incoming boolean signals are true.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "in0" },
        { "label": "in1", "optional": true }
      ],
      "dynamic": true,
      "min": 1,
      "max": 16
    },
    "params": [],
    "reads": ["@bool"],
    "writes": ["@bool"],
    "subtitle": "AND",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "or",
    "cat": "logic",
    "desc": "True when ANY incoming boolean signal is true.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "in0" },
        { "label": "in1", "optional": true }
      ],
      "dynamic": true,
      "min": 1,
      "max": 16
    },
    "params": [],
    "reads": ["@bool"],
    "writes": ["@bool"],
    "subtitle": "OR",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "not",
    "cat": "logic",
    "desc": "Inverts the incoming boolean signal.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "signal" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [],
    "reads": ["@bool"],
    "writes": ["@bool"],
    "subtitle": "NOT",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "position_size",
    "cat": "settings",
    "desc": "Fraction of allocated capital used per trade, from 0 to 1 (1 = 100%, 0.5 = 50%). Default: 1.",
    "compile_active": true,
    "inputs": {
      "ports": [],
      "dynamic": false,
      "min": 0,
      "max": 0
    },
    "params": [
      {
        "name": "size",
        "type": "number",
        "label": "size",
        "default": 1.0,
        "min": 0.0,
        "max": 1.0,
        "unit": "frac"
      }
    ],
    "reads": [],
    "writes": ["@setting"],
    "subtitle": "Size: 1 (100%)",
    "setting_key": "position_size",
    "ins": 0,
    "outs": 1
  },
  {
    "name": "stop_loss",
    "cat": "settings",
    "desc": "Fixed stop-loss as a percentage below/above entry. Default: 5.0%.",
    "compile_active": true,
    "inputs": {
      "ports": [],
      "dynamic": false,
      "min": 0,
      "max": 0
    },
    "params": [
      {
        "name": "pct",
        "type": "number",
        "label": "pct",
        "default": 5.0,
        "min": 0.0,
        "unit": "%",
        "optional": true
      }
    ],
    "reads": [],
    "writes": ["@setting"],
    "subtitle": "Stop: 5%",
    "setting_key": "stop_loss",
    "ins": 0,
    "outs": 1
  },
  {
    "name": "slippage",
    "cat": "settings",
    "desc": "Modeled slippage cost per leg in basis points. Default: 2.0 bps.",
    "compile_active": true,
    "inputs": {
      "ports": [],
      "dynamic": false,
      "min": 0,
      "max": 0
    },
    "params": [
      { "name": "bps", "type": "number", "label": "bps", "default": 2.0, "min": 0.0, "unit": "bps" }
    ],
    "reads": [],
    "writes": ["@setting"],
    "subtitle": "Slippage: 2 bps",
    "setting_key": "slippage_bps",
    "ins": 0,
    "outs": 1
  },
  {
    "name": "commission",
    "cat": "settings",
    "desc": "Per-share commission rate and minimum per order. Defaults match Alpaca (free).",
    "compile_active": true,
    "inputs": {
      "ports": [],
      "dynamic": false,
      "min": 0,
      "max": 0
    },
    "params": [
      {
        "name": "per_share_rate",
        "type": "number",
        "label": "per share",
        "default": 0.0,
        "min": 0.0,
        "unit": "$/share"
      },
      {
        "name": "min_per_order",
        "type": "number",
        "label": "min per order",
        "default": 0.0,
        "min": 0.0,
        "unit": "$"
      }
    ],
    "reads": [],
    "writes": ["@setting"],
    "subtitle": "Commission: free",
    "setting_key": "commission",
    "ins": 0,
    "outs": 1
  },
  {
    "name": "trailing_stop",
    "cat": "settings",
    "desc": "Trailing stop. type=pct trails value % from the peak; type=atr trails value x ATR(14). Optionally waits until the trade is activate_pct % in profit.",
    "compile_active": true,
    "inputs": {
      "ports": [],
      "dynamic": false,
      "min": 0,
      "max": 0
    },
    "params": [
      {
        "name": "type",
        "type": "select",
        "label": "type",
        "default": "pct",
        "options": ["pct", "atr"]
      },
      {
        "name": "value",
        "type": "number",
        "label": "value",
        "default": 5.0,
        "min": 0.0,
        "unit": "% or x ATR"
      },
      {
        "name": "source",
        "type": "select",
        "label": "source",
        "default": "high",
        "options": ["high", "close"]
      },
      {
        "name": "activate_on_profit",
        "type": "bool",
        "label": "activate on profit",
        "default": false
      },
      {
        "name": "activate_pct",
        "type": "number",
        "label": "activate pct",
        "default": 0.0,
        "min": 0.0,
        "unit": "%"
      }
    ],
    "reads": [],
    "writes": ["@setting"],
    "subtitle": "Trail: 5%",
    "setting_key": "trailing_stop",
    "ins": 0,
    "outs": 1
  },
  {
    "name": "entry",
    "cat": "output",
    "desc": "Entry terminal. Wire the buy-signal boolean here to trigger long entries.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "signal" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [],
    "reads": ["@bool"],
    "writes": [],
    "subtitle": "Entry",
    "setting_key": null,
    "ins": 1,
    "outs": 0
  },
  {
    "name": "exit",
    "cat": "output",
    "desc": "Exit terminal. Wire the sell-signal boolean here to trigger exits.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "signal", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [],
    "reads": ["@bool"],
    "writes": [],
    "subtitle": "Exit",
    "setting_key": null,
    "ins": 1,
    "outs": 0
  },
  {
    "name": "size",
    "cat": "output",
    "desc": "(T4) Size terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
    "compile_active": false,
    "inputs": {
      "ports": [
        { "label": "signal", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [],
    "reads": ["@bool"],
    "writes": [],
    "subtitle": "Size (T4)",
    "setting_key": null,
    "ins": 1,
    "outs": 0
  },
  {
    "name": "stop",
    "cat": "output",
    "desc": "(T4) Stop terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
    "compile_active": false,
    "inputs": {
      "ports": [
        { "label": "signal", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [],
    "reads": ["@bool"],
    "writes": [],
    "subtitle": "Stop (T4)",
    "setting_key": null,
    "ins": 1,
    "outs": 0
  }
];
