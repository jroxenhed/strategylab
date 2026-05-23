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
