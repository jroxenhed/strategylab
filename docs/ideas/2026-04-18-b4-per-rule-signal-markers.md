# B4 — Per-rule signal visualization

Sketchpad. Not a spec. Ideation for the B4 TODO item (per-rule signal visualization toggles).

## TODO text (for reference)

> **B4** Per-rule signal visualization toggles — eye icon on each rule row in strategy builder; when enabled, that rule's signals show as markers on the main chart during/after backtest. Replaces current hardcoded signal marker behavior. State stored with rule fields, persists with save/load. No global master toggle.

User refinement during ideation: **add** a global switch for markers on the main chart, keep per-rule on/off. Think forward to a dozen+ new rule types (MTC RSI, pattern recognition, etc.).

## Guiding principle

**Every marker must earn its place.** A marker earns it only when it adds context that isn't already visible from the indicator itself. Current precedent:

- RSI rule markers are *not* replicated on the main chart — the RSI pane (sidebar toggle) already shows the rule's truth state via the line.
- Buy/sell markers *are* replicated on the RSI pane — that crosstalk (trade decision × indicator state) is where strategy evaluation actually happens.

This earning test scales. With 12+ rule types, naively showing every rule's state everywhere becomes noise; the test tells you which markers to omit.

## Core reframe

**Decision markers are primary; rule-state markers are supplementary.**

The main product is *attributed trade markers* — buy/sell arrows that show which rules contributed at the moment a trade fired. Per-rule state visibility is a secondary tool, useful mainly for the minority of rules whose truth state isn't already drawn somewhere.

## Refined ideas

### 1. Classify rules by whether their state is natively rendered

- **Rendered** (RSI, MACD, BB, MA crossover, EMA touch): indicator pane or main-chart overlay already shows it. Per-rule markers duplicate info → **default OFF**, eye toggle still available for audit.
- **Hidden** (MTC RSI, pattern recognition, divergence, volume anomaly, support/resistance): no native visual. Markers genuinely earn their place → **default ON** when global switch is on.

Implementation hook: a `visible_in_native_pane: bool` flag on each rule type sets the default eye-toggle state. User can override per rule.

### 2. Attribution on the decision marker

Buy/sell arrow carries a compact row of colored dots = which rules contributed at that bar. Hover reveals names and values. Replaces the need for most continuous state markers — "why did we fire here?" is answered at the point of action, not via 200 background dots across the series.

### 3. Multi-pane replication follows evaluation value, not completeness

Buy/sell replicates to the panes whose indicators *participated in the decision* — not to every pane by default. MA-only trade → don't clutter the RSI pane. Small tightening of current behavior but scales well as panes proliferate.

### 4. Pattern recognition = ephemeral geometry, not persistent markers

Engulfing / H&S / divergence: draw the actual pattern lines (swing-point polylines, fade over N bars after detection). The pattern shape *is* the signal — a dot next to it adds nothing.

### 5. MTC rules = mini-track, not markers

For "15m RSI < 30 on a 5m chart": don't scatter markers across bars each time it's true. Add a thin colored strip along the top of the RSI pane showing the higher-timeframe RSI state as a continuous band. Zero markers, always-on, dense info. Generalize to any other-timeframe rule.

### 6. Hover-to-audit as the universal escape hatch

Hovering any bar shows a small popover listing every rule's state at that bar. The per-rule eye toggle is then for *persistent* inspection ("scan where this rule fired across the whole run"), not for ad-hoc questions. Reduces the temptation to leave toggles on.

### 7. Signal-kind taxonomy (visual grammar, not per-rule invention)

New rule types don't get new visuals; they slot into a fixed vocabulary:

- **Point events** (crossover, pattern match, turns_up) → arrow/triangle at the bar
- **State windows** (RSI<30, price above BB) → faint band or tiny dot-row; rendered as *transition-only* markers (enter/leave the state) to keep density sane
- **Slope/rate changes** (decelerating) → small chevron at the inflection bar
- **Structure** (H&S, support, trendline) → drawn line segments, not markers

Color carries *which rule*; shape carries *what kind of signal*. Scales to 12+ rules without a legend explosion.

## Usability implications

- **Global switch** gates all markers (including buy/sell).
- **Per-rule eye toggle** controls persistent per-rule markers only. Most users will touch maybe 1-2 toggles per strategy, not 12.
- Defaults do most of the work — the "rendered vs hidden" classification means users rarely need to reason about visibility explicitly.

## Open questions

- **#2 vs #6 redundancy** — does attribution-on-decision-markers fully replace the need for continuous per-rule markers on "rendered" rules, or do both coexist? Biggest judgment call.
- Exact visual vocabulary for the attribution dot-row on buy/sell markers (color discrimination ceiling with 6+ active rules).
- How pattern geometry (#4) interacts with chart persistence — fade timing, redraw on pan/zoom.
- Whether MTC mini-track (#5) belongs in the indicator pane or as its own micro-pane.

## Load-bearing trio

If implementing incrementally: **1 + 2 + transition-only rendering from 7** is the minimum that makes the feature non-noisy at 12+ rule types. 3, 4, 5 are follow-ups tied to specific new rule types landing.
