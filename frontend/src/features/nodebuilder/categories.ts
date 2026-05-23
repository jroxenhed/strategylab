/**
 * Node category palette mapping.
 *
 * Colors reference CSS custom properties defined in tokens.css and scoped to
 * `.nodebuilder-root`. Glyphs appear in node-header badges and Tab-menu icons.
 */

export const CATS = {
  ticker:     { color: "var(--nb-cat-ticker)",     glyph: "T" },
  data:       { color: "var(--nb-cat-data)",       glyph: "D" },
  indicator:  { color: "var(--nb-cat-indicator)",  glyph: "I" },
  signal:     { color: "var(--nb-cat-signal)",     glyph: "Σ" },  // Σ
  comparison: { color: "var(--nb-cat-comparison)", glyph: "C" },
  logic:      { color: "var(--nb-cat-logic)",      glyph: "L" },
  rules:      { color: "var(--nb-cat-rules)",      glyph: "R" },
  settings:   { color: "var(--nb-cat-settings)",   glyph: "S" },
  code:       { color: "var(--nb-cat-code)",       glyph: "{}" },
  output:     { color: "var(--nb-cat-output)",     glyph: "O" },
} as const;

export type CatKey = keyof typeof CATS;
