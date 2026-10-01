/**
 * Unit 2 frontend tests: NODE_CATALOG consistency.
 *
 * Run with: cd frontend && npx vitest run src/features/nodebuilder/__tests__/catalog.test.ts
 */

import { describe, it, expect } from "vitest";
import {
  NODE_CATALOG,
  getNode,
  catalogByCategory,
  SOURCE_OPTIONS,
  RSI_TYPE_OPTIONS,
  TRAILING_STOP_DEFAULTS,
  TRAILING_STOP_SOURCE_OPTIONS,
  TRAILING_STOP_TYPE_OPTIONS,
  hasInputPort,
} from "../catalog";
import { GENERATED_CATALOG } from "../catalog.generated";
import { CATS } from "../categories";

// Minimum required categories that the backend also asserts.
const REQUIRED_CATEGORIES = new Set([
  "ticker",
  "indicator",
  "comparison",
  "logic",
  "settings",
  "output",
]);

describe("NODE_CATALOG integrity", () => {
  it("is non-empty", () => {
    expect(NODE_CATALOG.length).toBeGreaterThan(0);
  });

  it("has unique names", () => {
    const names = NODE_CATALOG.map((e) => e.name);
    const unique = new Set(names);
    expect(unique.size).toBe(names.length);
  });

  it("every entry's cat is a known CATS key", () => {
    const knownCats = new Set(Object.keys(CATS));
    const unknown = NODE_CATALOG.filter((e) => !knownCats.has(e.cat));
    expect(unknown).toEqual([]);
  });

  it("every entry has non-empty reads OR non-empty writes", () => {
    // merge (W2) is a pass-through: no params, it hands on the union of its
    // inputs, so it has nothing of its own to read or write.
    const passThrough = new Set(["merge"]);
    for (const name of passThrough) {
      expect(Object.keys(getNode(name).defaults.params)).toEqual([]);
    }
    const bothEmpty = NODE_CATALOG.filter(
      (e) => e.reads.length === 0 && e.writes.length === 0 && !passThrough.has(e.name)
    );
    expect(bothEmpty).toEqual([]);
  });

  it("compileActive=false ONLY for 'size' and 'stop'", () => {
    const inactive = NODE_CATALOG.filter((e) => !e.compileActive).map((e) => e.name);
    expect(new Set(inactive)).toEqual(new Set(["size", "stop"]));
  });

  it("all reads/writes attributes start with '@'", () => {
    for (const entry of NODE_CATALOG) {
      for (const attr of entry.reads) {
        expect(attr, `entry "${entry.name}" reads attr "${attr}" lacks '@'`).toMatch(/^@/);
      }
      for (const attr of entry.writes) {
        expect(attr, `entry "${entry.name}" writes attr "${attr}" lacks '@'`).toMatch(/^@/);
      }
    }
  });

  it("defaults has required keys: params, ins, outs, subtitle", () => {
    const required = ["params", "ins", "outs", "subtitle"] as const;
    for (const entry of NODE_CATALOG) {
      for (const key of required) {
        expect(
          entry.defaults,
          `entry "${entry.name}" defaults missing key "${key}"`
        ).toHaveProperty(key);
      }
    }
  });

  it("settings nodes have a setting_key in defaults", () => {
    const settingsEntries = NODE_CATALOG.filter((e) => e.cat === "settings");
    expect(settingsEntries.length).toBeGreaterThan(0);
    for (const entry of settingsEntries) {
      expect(
        entry.defaults,
        `settings entry "${entry.name}" missing setting_key`
      ).toHaveProperty("setting_key");
      expect(typeof entry.defaults.setting_key).toBe("string");
    }
  });
});

describe("getNode()", () => {
  it("returns the RSI entry for getNode('rsi')", () => {
    const entry = getNode("rsi");
    expect(entry.name).toBe("rsi");
    expect(entry.cat).toBe("indicator");
    expect(entry.writes).toContain("@rsi");
  });

  it("throws for an unknown name", () => {
    expect(() => getNode("nonexistent")).toThrow();
  });

  it("throws with a message containing the name", () => {
    expect(() => getNode("foobar")).toThrow(/foobar/);
  });
});

describe("catalogByCategory()", () => {
  it("covers all required categories", () => {
    const grouped = catalogByCategory();
    const present = new Set(Object.keys(grouped));
    for (const cat of REQUIRED_CATEGORIES) {
      expect(present, `missing required category "${cat}"`).toContain(cat);
    }
  });

  it("contains every catalog entry exactly once", () => {
    const grouped = catalogByCategory();
    const allNamesGrouped = Object.values(grouped)
      .flat()
      .map((e) => e.name)
      .sort();
    const allNamesCatalog = [...NODE_CATALOG].map((e) => e.name).sort();
    expect(allNamesGrouped).toEqual(allNamesCatalog);
  });

  it("each category array is non-empty", () => {
    const grouped = catalogByCategory();
    for (const [cat, entries] of Object.entries(grouped)) {
      expect(entries.length, `category "${cat}" is empty`).toBeGreaterThan(0);
    }
  });
});

describe("NODE_CATALOG.paramTypes (F277 — drift guard)", () => {
  it("every paramTypes key is also a key in defaults.params", () => {
    for (const entry of NODE_CATALOG) {
      if (!entry.paramTypes) continue;
      const defaultKeys = new Set(Object.keys(entry.defaults.params));
      for (const key of Object.keys(entry.paramTypes)) {
        expect(
          defaultKeys.has(key),
          `node "${entry.name}" has paramTypes["${key}"] but no defaults.params["${key}"]`,
        ).toBe(true);
      }
    }
  });

  it("every paramTypes entry of type 'select' has a non-empty options array", () => {
    for (const entry of NODE_CATALOG) {
      if (!entry.paramTypes) continue;
      for (const [key, spec] of Object.entries(entry.paramTypes)) {
        if (spec.type !== 'select') continue;
        expect(
          Array.isArray(spec.options) && spec.options.length > 0,
          `node "${entry.name}" paramTypes["${key}"].type is 'select' but options is empty/missing`,
        ).toBe(true);
      }
    }
  });

  it("every numeric default has an explicit paramTypes entry (no silent inference for canonical params)", () => {
    // Soft contract: any param whose default is a `number` should be explicitly
    // typed so future readers can trust the catalog over typeof-inference.
    // (Strings without enums may rely on inference until F271+ extends them.)
    for (const entry of NODE_CATALOG) {
      for (const [key, value] of Object.entries(entry.defaults.params)) {
        if (typeof value !== 'number') continue;
        expect(
          entry.paramTypes?.[key]?.type,
          `node "${entry.name}" param "${key}" defaults to a number but is not declared in paramTypes`,
        ).toBe('number');
      }
    }
  });
});

describe("catalog honesty (F435 0.G)", () => {
  it("offers only the data providers the backend registers (no polygon)", () => {
    expect(SOURCE_OPTIONS).not.toContain("polygon");
    expect([...SOURCE_OPTIONS]).toEqual(["yahoo", "alpaca", "alpaca-iex", "ibkr"]);
    // Since W2 the Ticker has no source param: the sidebar (backtest) and the
    // spawn dialog (bots) own the data source (plan D11).
    expect(getNode("ticker").paramTypes?.source).toBeUndefined();
    expect(getNode("ticker").defaults.params).not.toHaveProperty("source");
  });

  it("RSI type options are exactly what compute_rsi accepts", () => {
    expect([...RSI_TYPE_OPTIONS]).toEqual(["sma", "wilder"]);
    expect(getNode("rsi").paramTypes?.type?.options).toEqual(RSI_TYPE_OPTIONS);
  });

  it("RSI default type is wilder (the rule builder default) and is an offered option", () => {
    const rsi = getNode("rsi");
    expect(rsi.defaults.params.type).toBe("wilder");
    expect(rsi.paramTypes?.type?.options).toContain(rsi.defaults.params.type);
  });

  it("every select default is one of its own options", () => {
    // Compared as text: a select shows String(value), so a boolean default
    // (trailing stop activate_on_profit) must match the "false"/"true" option.
    for (const entry of NODE_CATALOG) {
      for (const [key, spec] of Object.entries(entry.paramTypes ?? {})) {
        if (spec.type !== "select") continue;
        const value = entry.defaults.params[key];
        if (Array.isArray(value)) {
          // A multi-select (day_of_week.days): every item is an option.
          for (const item of value) {
            expect(spec.options, `node "${entry.name}" default item for "${key}" is not an option`)
              .toContain(String(item));
          }
          continue;
        }
        expect(
          spec.options,
          `node "${entry.name}" default for "${key}" is not an option`,
        ).toContain(String(entry.defaults.params[key]));
      }
    }
  });

  it("the Size/Stop (T4) stubs are flagged not compile-active, so the Tab menu hides them", () => {
    expect(getNode("size").compileActive).toBe(false);
    expect(getNode("stop").compileActive).toBe(false);
    const placeable = NODE_CATALOG.filter((e) => e.compileActive).map((e) => e.name);
    expect(placeable).not.toContain("size");
    expect(placeable).not.toContain("stop");
  });

  it("Position Size is labelled as a fraction, and its default fits the backend range (0, 1]", () => {
    const ps = getNode("position_size");
    expect(ps.paramTypes?.size?.unit).toBe("fraction");
    const size = ps.defaults.params.size as number;
    expect(size).toBeGreaterThan(0);
    expect(size).toBeLessThanOrEqual(1);
    // The chip text must show the stored value, not only a percent.
    expect(ps.defaults.subtitle).toContain(String(size));
  });

  it("every numeric settings param declares a unit", () => {
    // Choice params (a select such as trailing stop type) have no unit.
    for (const entry of NODE_CATALOG.filter((e) => e.cat === "settings")) {
      for (const [key, value] of Object.entries(entry.defaults.params)) {
        if (typeof value !== "number") continue;
        expect(
          entry.paramTypes?.[key]?.unit,
          `settings node "${entry.name}" param "${key}" has no unit`,
        ).toBeTruthy();
      }
    }
  });
});

describe("trailing_stop settings node (F435 0.A)", () => {
  it("is a compile-active settings node with TrailingStopConfig's fields and defaults", () => {
    const ts = getNode("trailing_stop");
    expect(ts.cat).toBe("settings");
    expect(ts.compileActive).toBe(true);
    expect(ts.defaults.setting_key).toBe("trailing_stop");
    // Since W2 a settings node also writes its value as a detail attribute
    // (the `write` param out); the config fields are the rest.
    const { out, ...config } = ts.defaults.params;
    expect(out).toBe("@trail_value");
    expect(ts.params?.find((p) => p.name === "out")?.type).toBe("write");
    expect(config).toEqual({
      type: "pct", value: 5, source: "high", activate_on_profit: false, activate_pct: 0,
    });
    expect(config).toEqual({ ...TRAILING_STOP_DEFAULTS });
  });

  it("offers only the types and sources the simulator reads", () => {
    const ts = getNode("trailing_stop");
    expect([...TRAILING_STOP_TYPE_OPTIONS]).toEqual(["pct", "atr"]);
    expect([...TRAILING_STOP_SOURCE_OPTIONS]).toEqual(["high", "close"]);
    expect(ts.paramTypes?.type?.options).toEqual(TRAILING_STOP_TYPE_OPTIONS);
    expect(ts.paramTypes?.source?.options).toEqual(TRAILING_STOP_SOURCE_OPTIONS);
    expect(ts.paramTypes?.activate_on_profit?.options).toEqual(["false", "true"]);
  });

  it("labels its numbers with units", () => {
    const ts = getNode("trailing_stop");
    expect(ts.paramTypes?.value).toEqual({ type: "number", unit: "% or x ATR" });
    expect(ts.paramTypes?.activate_pct).toEqual({ type: "number", unit: "%" });
  });
});

describe("catalog is built from catalog.generated.ts (F435 1.D, D9)", () => {
  it("has the same entries, in the same order, as the generated backend catalog", () => {
    expect(NODE_CATALOG.map((e) => e.name)).toEqual(GENERATED_CATALOG.map((g) => g.name));
    for (const g of GENERATED_CATALOG) {
      const e = getNode(g.name);
      expect(e.cat).toBe(g.cat);
      expect(e.desc).toBe(g.desc);
      expect(e.compileActive).toBe(g.compile_active);
      expect(e.reads).toEqual(g.reads);
      expect(e.writes).toEqual(g.writes);
      expect(e.defaults.subtitle).toBe(g.subtitle);
    }
  });

  it("every entry carries its ParamSpec list and PortsSpec", () => {
    for (const entry of NODE_CATALOG) {
      expect(entry.params, `node "${entry.name}" has no params`).toBeDefined();
      expect(entry.inputs, `node "${entry.name}" has no inputs`).toBeDefined();
    }
  });

  it("defaults.params and paramTypes come from the ParamSpecs, key for key", () => {
    for (const entry of NODE_CATALOG) {
      const specs = entry.params ?? [];
      expect(Object.keys(entry.defaults.params)).toEqual(specs.map((p) => p.name));
      for (const p of specs) {
        expect(entry.defaults.params[p.name]).toEqual(p.default);
        expect(entry.paramTypes?.[p.name], `node "${entry.name}" param "${p.name}"`).toBeDefined();
      }
    }
  });

  it("int and number specs render as number fields, bool as a false/true select", () => {
    expect(getNode("rsi").params?.find((p) => p.name === "period")?.type).toBe("int");
    expect(getNode("rsi").paramTypes?.period).toEqual({ type: "number" });
    expect(getNode("trailing_stop").params?.find((p) => p.name === "activate_on_profit")?.type).toBe("bool");
  });

  it("the node row shows 'fraction' for frac and hides 'bars'", () => {
    expect(getNode("position_size").params?.[0].unit).toBe("frac");
    expect(getNode("position_size").paramTypes?.size?.unit).toBe("fraction");
    expect(getNode("sma").params?.[0].unit).toBe("bars");
    expect(getNode("sma").paramTypes?.period?.unit).toBeUndefined();
  });

  it("ticker symbol and interval are not code-able (D1)", () => {
    const params = getNode("ticker").params ?? [];
    expect(params.find((p) => p.name === "symbol")?.code_able).toBe(false);
    expect(params.find((p) => p.name === "interval")?.code_able).toBe(false);
  });

  it("comparisons have ports a and optional b; AND/OR are dynamic", () => {
    const cmp = getNode("crosses_above").inputs;
    expect(cmp?.ports).toEqual([{ label: "a" }, { label: "b", optional: true }]);
    expect(cmp?.dynamic).toBe(false);
    expect([cmp?.min, cmp?.max]).toEqual([1, 2]);
    expect(getNode("and").inputs?.dynamic).toBe(true);
    expect(getNode("or").inputs?.dynamic).toBe(true);
    expect(getNode("entry").inputs?.min).toBe(1);
  });

  it("hasInputPort follows PortsSpec.max: tickers and settings take no wire", () => {
    for (const entry of NODE_CATALOG) {
      expect(hasInputPort(entry.name), entry.name).toBe((entry.inputs?.max ?? 0) > 0);
    }
    expect(getNode("ticker").inputs?.max).toBe(0);
    expect(getNode("stop_loss").inputs?.max).toBe(0);
  });
});
