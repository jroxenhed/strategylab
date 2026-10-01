// GENERATED FILE. Do not edit by hand.
//
// Source: the backend node registry (backend/nodebuilder/trading/nodes_*.py,
// listed by backend/nodebuilder/nodes.py; plan decision D9).
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

/** A backend catalog entry, as NodeCatalogEntry.to_json() gives it. */
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
      }
    ],
    "reads": [],
    "writes": ["@open", "@high", "@low", "@close", "@volume", "@time", "@index"],
    "subtitle": null,
    "setting_key": null,
    "ins": 0,
    "outs": 5
  },
  {
    "name": "merge",
    "cat": "data",
    "desc": "Joins the streams on its inputs into one, so a node below can read from all of them.",
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
    "reads": [],
    "writes": [],
    "subtitle": "merge",
    "setting_key": null,
    "ins": 2,
    "outs": 0
  },
  {
    "name": "price",
    "cat": "data",
    "desc": "One price field (default @close) under its own name.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "field", "type": "attr", "label": "field", "default": "@close", "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@price", "dtype": "float" }
    ],
    "reads": ["@close"],
    "writes": ["@price"],
    "subtitle": "close",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "time_of_day",
    "cat": "data",
    "desc": "True when the bar starts inside the time range (New York time, from included, to excluded). Empty: the regular session 09:30-16:00.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "in", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      { "name": "range", "type": "time_range", "label": "range", "default": null, "optional": true },
      { "name": "out", "type": "write", "label": "out", "default": "@in_time", "dtype": "bool" }
    ],
    "reads": [],
    "writes": ["@in_time"],
    "subtitle": "session",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "day_of_week",
    "cat": "data",
    "desc": "True on the picked weekdays (New York date). No days picked: never true.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "in", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "days",
        "type": "select",
        "label": "days",
        "default": ["mon", "tue", "wed", "thu", "fri"],
        "options": ["mon", "tue", "wed", "thu", "fri"]
      },
      { "name": "out", "type": "write", "label": "out", "default": "@on_day", "dtype": "bool" }
    ],
    "reads": [],
    "writes": ["@on_day"],
    "subtitle": "M T W T F",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "session_bar",
    "cat": "data",
    "desc": "The bar's number in its day's regular session (0 = the first bar at or after 09:30 New York time). NaN outside 09:30-16:00 and on daily bars.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "in", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@session_bar",
        "dtype": "float"
      }
    ],
    "reads": [],
    "writes": ["@session_bar"],
    "subtitle": "bar #",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "rsi",
    "cat": "indicator",
    "desc": "Relative Strength Index. Default period=14, type=wilder (sma also available).",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      },
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@rsi", "dtype": "float" }
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
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      },
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      {
        "name": "out_line",
        "type": "write",
        "label": "out_line",
        "default": "@macd_line",
        "dtype": "float"
      },
      {
        "name": "out_signal",
        "type": "write",
        "label": "out_signal",
        "default": "@macd_signal",
        "dtype": "float"
      },
      {
        "name": "out_hist",
        "type": "write",
        "label": "out_hist",
        "default": "@macd_histogram",
        "dtype": "float"
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
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@sma", "dtype": "float" }
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
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@ema", "dtype": "float" }
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
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      },
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      {
        "name": "out_upper",
        "type": "write",
        "label": "out_upper",
        "default": "@bb_upper",
        "dtype": "float"
      },
      {
        "name": "out_middle",
        "type": "write",
        "label": "out_middle",
        "default": "@bb_middle",
        "dtype": "float"
      },
      {
        "name": "out_lower",
        "type": "write",
        "label": "out_lower",
        "default": "@bb_lower",
        "dtype": "float"
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
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "high", "type": "attr", "label": "high", "default": "@high", "dtype": "float" },
      { "name": "low", "type": "attr", "label": "low", "default": "@low", "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@atr", "dtype": "float" }
    ],
    "reads": ["@high", "@low", "@close"],
    "writes": ["@atr"],
    "subtitle": "ATR(14)",
    "setting_key": null,
    "ins": 3,
    "outs": 1
  },
  {
    "name": "stochastic",
    "cat": "indicator",
    "desc": "Stochastic oscillator: %K and %D. Defaults: k_period=14, d_period=3, smooth_k=3.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      {
        "name": "k_period",
        "type": "int",
        "label": "k_period",
        "default": 14,
        "min": 2,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "d_period",
        "type": "int",
        "label": "d_period",
        "default": 3,
        "min": 2,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "smooth_k",
        "type": "int",
        "label": "smooth_k",
        "default": 3,
        "min": 1,
        "max": 50,
        "unit": "bars"
      },
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "high", "type": "attr", "label": "high", "default": "@high", "dtype": "float" },
      { "name": "low", "type": "attr", "label": "low", "default": "@low", "dtype": "float" },
      {
        "name": "out_k",
        "type": "write",
        "label": "out_k",
        "default": "@stoch_k",
        "dtype": "float"
      },
      {
        "name": "out_d",
        "type": "write",
        "label": "out_d",
        "default": "@stoch_d",
        "dtype": "float"
      }
    ],
    "reads": ["@high", "@low", "@close"],
    "writes": ["@stoch_k", "@stoch_d"],
    "subtitle": "Stoch(14,3,3)",
    "setting_key": null,
    "ins": 1,
    "outs": 2
  },
  {
    "name": "adx",
    "cat": "indicator",
    "desc": "Average Directional Index with +DI and -DI. Default period=14.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "high", "type": "attr", "label": "high", "default": "@high", "dtype": "float" },
      { "name": "low", "type": "attr", "label": "low", "default": "@low", "dtype": "float" },
      {
        "name": "out_adx",
        "type": "write",
        "label": "out_adx",
        "default": "@adx",
        "dtype": "float"
      },
      {
        "name": "out_plus_di",
        "type": "write",
        "label": "out_plus_di",
        "default": "@plus_di",
        "dtype": "float"
      },
      {
        "name": "out_minus_di",
        "type": "write",
        "label": "out_minus_di",
        "default": "@minus_di",
        "dtype": "float"
      }
    ],
    "reads": ["@high", "@low", "@close"],
    "writes": ["@adx", "@plus_di", "@minus_di"],
    "subtitle": "ADX(14)",
    "setting_key": null,
    "ins": 1,
    "outs": 3
  },
  {
    "name": "atr_pct",
    "cat": "indicator",
    "desc": "ATR as a percent of the close (atr / close * 100). Default period=14.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "high", "type": "attr", "label": "high", "default": "@high", "dtype": "float" },
      { "name": "low", "type": "attr", "label": "low", "default": "@low", "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@atr_pct", "dtype": "float" }
    ],
    "reads": ["@high", "@low", "@close"],
    "writes": ["@atr_pct"],
    "subtitle": "ATR%(14)",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "volume",
    "cat": "indicator",
    "desc": "Volume: the raw volume (type raw) or its simple moving average (type sma, default period=20).",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      {
        "name": "type",
        "type": "select",
        "label": "type",
        "default": "raw",
        "options": ["raw", "sma"]
      },
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
        "name": "source",
        "type": "attr",
        "label": "source",
        "default": "@volume",
        "dtype": "float"
      },
      { "name": "out", "type": "write", "label": "out", "default": "@vol", "dtype": "float" }
    ],
    "reads": ["@volume"],
    "writes": ["@vol"],
    "subtitle": "volume",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "ma",
    "cat": "indicator",
    "desc": "Moving average of any type: sma, ema or rma (Wilder). Default period=20, type=ema.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "source" }
      ],
      "dynamic": false,
      "min": 1,
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
        "name": "type",
        "type": "select",
        "label": "type",
        "default": "ema",
        "options": ["sma", "ema", "rma"]
      },
      { "name": "source", "type": "attr", "label": "source", "default": null, "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@ma", "dtype": "float" }
    ],
    "reads": ["@close"],
    "writes": ["@ma"],
    "subtitle": "MA(20, ema)",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "constant",
    "cat": "signal",
    "desc": "A fixed number, on every bar (or as one detail value with as_detail).",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "in", "optional": true }
      ],
      "dynamic": false,
      "min": 0,
      "max": 1
    },
    "params": [
      { "name": "value", "type": "number", "label": "value", "default": 0.0 },
      { "name": "as_detail", "type": "bool", "label": "as detail", "default": false },
      { "name": "out", "type": "write", "label": "out", "default": "@const", "dtype": "float" }
    ],
    "reads": [],
    "writes": ["@const"],
    "subtitle": "constant",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "math",
    "cat": "signal",
    "desc": "a op b bar by bar: add, sub, mul, div, min, max; or abs / neg of a. Dividing by zero gives NaN.",
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
        "name": "op",
        "type": "select",
        "label": "op",
        "default": "add",
        "options": ["add", "sub", "mul", "div", "min", "max", "abs", "neg"]
      },
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "b",
        "type": "attr",
        "label": "b",
        "default": null,
        "dtype": "float",
        "optional": true
      },
      { "name": "out", "type": "write", "label": "out", "default": "@math", "dtype": "float" }
    ],
    "reads": ["@a", "@b"],
    "writes": ["@math"],
    "subtitle": "a + b",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "shift",
    "cat": "signal",
    "desc": "a as it was `bars` bars ago. Default bars=1.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "bars",
        "type": "int",
        "label": "bars",
        "default": 1,
        "min": 1,
        "max": 500,
        "unit": "bars"
      },
      { "name": "out", "type": "write", "label": "out", "default": "@shift", "dtype": "float" }
    ],
    "reads": ["@a"],
    "writes": ["@shift"],
    "subtitle": "shift 1",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "rolling",
    "cat": "signal",
    "desc": "mean, min, max, std or sum of a over the last `window` bars. Default mean over 20.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "op",
        "type": "select",
        "label": "op",
        "default": "mean",
        "options": ["mean", "min", "max", "std", "sum"]
      },
      {
        "name": "window",
        "type": "int",
        "label": "window",
        "default": 20,
        "min": 2,
        "max": 500,
        "unit": "bars"
      },
      { "name": "out", "type": "write", "label": "out", "default": "@rolling", "dtype": "float" }
    ],
    "reads": ["@a"],
    "writes": ["@rolling"],
    "subtitle": "mean(20)",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "rising",
    "cat": "signal",
    "desc": "True when a is higher than on the bar before.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@rising", "dtype": "bool" }
    ],
    "reads": ["@a"],
    "writes": ["@rising"],
    "subtitle": "rising",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "falling",
    "cat": "signal",
    "desc": "True when a is lower than on the bar before.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      { "name": "out", "type": "write", "label": "out", "default": "@falling", "dtype": "bool" }
    ],
    "reads": ["@a"],
    "writes": ["@falling"],
    "subtitle": "falling",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "rising_over",
    "cat": "signal",
    "desc": "True when a is higher than it was `bars` bars ago. Default bars=10.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "bars",
        "type": "int",
        "label": "bars",
        "default": 10,
        "min": 0,
        "max": 500,
        "unit": "bars"
      },
      { "name": "out", "type": "write", "label": "out", "default": "@rising_over", "dtype": "bool" }
    ],
    "reads": ["@a"],
    "writes": ["@rising_over"],
    "subtitle": "rising over",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "falling_over",
    "cat": "signal",
    "desc": "True when a is lower than it was `bars` bars ago. Default bars=10.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "bars",
        "type": "int",
        "label": "bars",
        "default": 10,
        "min": 0,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@falling_over",
        "dtype": "bool"
      }
    ],
    "reads": ["@a"],
    "writes": ["@falling_over"],
    "subtitle": "falling over",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "turns_up",
    "cat": "signal",
    "desc": "True when a starts rising after falling: the last `bars` steps rose and the one before fell. Optional min move in percent from the low point.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "bars",
        "type": "int",
        "label": "bars",
        "default": 1,
        "min": 1,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "min_pct",
        "type": "number",
        "label": "min move",
        "default": null,
        "min": 0.0,
        "unit": "%",
        "optional": true
      },
      { "name": "out", "type": "write", "label": "out", "default": "@turns_up", "dtype": "bool" }
    ],
    "reads": ["@a"],
    "writes": ["@turns_up"],
    "subtitle": "turns up",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "turns_down",
    "cat": "signal",
    "desc": "True when a starts falling after rising: the last `bars` steps fell and the one before rose. Optional min move in percent from the high point.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "bars",
        "type": "int",
        "label": "bars",
        "default": 1,
        "min": 1,
        "max": 500,
        "unit": "bars"
      },
      {
        "name": "min_pct",
        "type": "number",
        "label": "min move",
        "default": null,
        "min": 0.0,
        "unit": "%",
        "optional": true
      },
      { "name": "out", "type": "write", "label": "out", "default": "@turns_down", "dtype": "bool" }
    ],
    "reads": ["@a"],
    "writes": ["@turns_down"],
    "subtitle": "turns down",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "turns_up_below",
    "cat": "signal",
    "desc": "True when a rises from a bar that was below the threshold.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      { "name": "threshold", "type": "number", "label": "threshold", "default": null },
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@turns_up_below",
        "dtype": "bool"
      }
    ],
    "reads": ["@a"],
    "writes": ["@turns_up_below"],
    "subtitle": "turns up below",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "turns_down_above",
    "cat": "signal",
    "desc": "True when a falls from a bar that was above the threshold.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      { "name": "threshold", "type": "number", "label": "threshold", "default": null },
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@turns_down_above",
        "dtype": "bool"
      }
    ],
    "reads": ["@a"],
    "writes": ["@turns_down_above"],
    "subtitle": "turns down above",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "accelerating",
    "cat": "signal",
    "desc": "True when a's step from the bar before is bigger than the step before it.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@accelerating",
        "dtype": "bool"
      }
    ],
    "reads": ["@a"],
    "writes": ["@accelerating"],
    "subtitle": "accelerating",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "decelerating",
    "cat": "signal",
    "desc": "True when a's step from the bar before is smaller than the step before it.",
    "compile_active": true,
    "inputs": {
      "ports": [
        { "label": "a" }
      ],
      "dynamic": false,
      "min": 1,
      "max": 1
    },
    "params": [
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@decelerating",
        "dtype": "bool"
      }
    ],
    "reads": ["@a"],
    "writes": ["@decelerating"],
    "subtitle": "decelerating",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "crosses_above",
    "cat": "comparison",
    "desc": "True on the bar where a crosses above b (or the threshold).",
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
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "b",
        "type": "attr",
        "label": "b",
        "default": null,
        "dtype": "float",
        "optional": true
      },
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      },
      { "name": "out", "type": "write", "label": "out", "default": "@xa", "dtype": "bool" }
    ],
    "reads": ["@a", "@b"],
    "writes": ["@xa"],
    "subtitle": "crosses above",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "crosses_below",
    "cat": "comparison",
    "desc": "True on the bar where a crosses below b (or the threshold).",
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
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "b",
        "type": "attr",
        "label": "b",
        "default": null,
        "dtype": "float",
        "optional": true
      },
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      },
      { "name": "out", "type": "write", "label": "out", "default": "@xb", "dtype": "bool" }
    ],
    "reads": ["@a", "@b"],
    "writes": ["@xb"],
    "subtitle": "crosses below",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "above",
    "cat": "comparison",
    "desc": "True when a is above b (or the threshold).",
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
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "b",
        "type": "attr",
        "label": "b",
        "default": null,
        "dtype": "float",
        "optional": true
      },
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      },
      { "name": "out", "type": "write", "label": "out", "default": "@above", "dtype": "bool" }
    ],
    "reads": ["@a", "@b"],
    "writes": ["@above"],
    "subtitle": "above",
    "setting_key": null,
    "ins": 2,
    "outs": 1
  },
  {
    "name": "below",
    "cat": "comparison",
    "desc": "True when a is below b (or the threshold).",
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
      { "name": "a", "type": "attr", "label": "a", "default": null, "dtype": "float" },
      {
        "name": "b",
        "type": "attr",
        "label": "b",
        "default": null,
        "dtype": "float",
        "optional": true
      },
      {
        "name": "threshold",
        "type": "number",
        "label": "threshold",
        "default": null,
        "optional": true
      },
      { "name": "out", "type": "write", "label": "out", "default": "@below", "dtype": "bool" }
    ],
    "reads": ["@a", "@b"],
    "writes": ["@below"],
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
    "params": [
      { "name": "terms", "type": "attr_list", "label": "terms", "default": null, "dtype": "bool" },
      { "name": "out", "type": "write", "label": "out", "default": "@and", "dtype": "bool" }
    ],
    "reads": ["@bool"],
    "writes": ["@and"],
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
    "params": [
      { "name": "terms", "type": "attr_list", "label": "terms", "default": null, "dtype": "bool" },
      { "name": "out", "type": "write", "label": "out", "default": "@or", "dtype": "bool" }
    ],
    "reads": ["@bool"],
    "writes": ["@or"],
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
    "params": [
      { "name": "signal", "type": "attr", "label": "signal", "default": null, "dtype": "bool" },
      { "name": "out", "type": "write", "label": "out", "default": "@not", "dtype": "bool" }
    ],
    "reads": ["@bool"],
    "writes": ["@not"],
    "subtitle": "NOT",
    "setting_key": null,
    "ins": 1,
    "outs": 1
  },
  {
    "name": "xor",
    "cat": "logic",
    "desc": "True when an odd number of incoming boolean signals are true (two signals: exactly one).",
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
    "params": [
      { "name": "terms", "type": "attr_list", "label": "terms", "default": null, "dtype": "bool" },
      { "name": "out", "type": "write", "label": "out", "default": "@xor", "dtype": "bool" }
    ],
    "reads": ["@bool"],
    "writes": ["@xor"],
    "subtitle": "XOR",
    "setting_key": null,
    "ins": 2,
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
      },
      { "name": "out", "type": "write", "label": "out", "default": "@size_frac", "dtype": "float" }
    ],
    "reads": [],
    "writes": ["@size_frac"],
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
      },
      { "name": "out", "type": "write", "label": "out", "default": "@stop_pct", "dtype": "float" }
    ],
    "reads": [],
    "writes": ["@stop_pct"],
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
      { "name": "bps", "type": "number", "label": "bps", "default": 2.0, "min": 0.0, "unit": "bps" },
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@slippage_bps",
        "dtype": "float"
      }
    ],
    "reads": [],
    "writes": ["@slippage_bps"],
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
      },
      {
        "name": "out_rate",
        "type": "write",
        "label": "out_rate",
        "default": "@per_share_rate",
        "dtype": "float"
      },
      {
        "name": "out_min",
        "type": "write",
        "label": "out_min",
        "default": "@min_per_order",
        "dtype": "float"
      }
    ],
    "reads": [],
    "writes": ["@per_share_rate", "@min_per_order"],
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
      },
      {
        "name": "out",
        "type": "write",
        "label": "out",
        "default": "@trail_value",
        "dtype": "float"
      }
    ],
    "reads": [],
    "writes": ["@trail_value"],
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
    "params": [
      { "name": "signal", "type": "attr", "label": "signal", "default": null, "dtype": "bool" }
    ],
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
    "params": [
      {
        "name": "signal",
        "type": "attr",
        "label": "signal",
        "default": null,
        "dtype": "bool",
        "optional": true
      }
    ],
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
