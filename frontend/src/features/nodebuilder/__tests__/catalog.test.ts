/**
 * Unit 2 frontend tests: NODE_CATALOG consistency.
 *
 * Run with: cd frontend && npx vitest run src/features/nodebuilder/__tests__/catalog.test.ts
 */

import { describe, it, expect } from "vitest";
import { NODE_CATALOG, getNode, catalogByCategory } from "../catalog";
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
    const bothEmpty = NODE_CATALOG.filter(
      (e) => e.reads.length === 0 && e.writes.length === 0
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
