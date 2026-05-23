"""StrategyRequest -> Graph auto-render (canonical Python impl).

Unit 3 — public surface is `auto_render(req: StrategyRequest) -> Graph`.

The returned graph always has readOnly=True and _version=1.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from models import StrategyRequest, RegimeConfig
from signal_engine import Rule, migrate_rule
from nodebuilder.models import Graph, Node, Wire


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_COL_TICKER = 0.0
_COL_INDICATOR = 200.0
_COL_COMPARISON = 400.0
_COL_LOGIC = 600.0
_COL_REGIME_GATE = 800.0
_COL_TERMINAL = 1000.0
_ROW_PITCH = 80.0
_SETTINGS_X = 200.0
_SETTINGS_Y_START = 600.0
_SETTINGS_Y_PITCH = 100.0
_REGIME_Y_OFFSET = 400.0


# ---------------------------------------------------------------------------
# Indicator name + param resolution
# ---------------------------------------------------------------------------

def _resolve_indicator(rule: Rule) -> tuple[str, dict[str, Any]]:
    """Return (catalog_node_name, params_dict) for a migrated rule's indicator.

    Assumes migrate_rule() has already been called.
    """
    ind = rule.indicator
    rp = rule.params or {}

    if ind == "rsi":
        return "rsi", {"period": rp.get("period", 14), "type": rp.get("type", "sma")}
    if ind == "macd":
        return "macd", {
            "fast": rp.get("fast", 12),
            "slow": rp.get("slow", 26),
            "signal": rp.get("signal", 9),
        }
    if ind == "ma":
        ma_type = rp.get("type", "sma")
        node_name = "ema" if ma_type == "ema" else "sma"
        return node_name, {"period": rp.get("period", 20)}
    if ind == "bb":
        return "bollinger", {"period": rp.get("period", 20), "stddev": rp.get("stddev", 2.0)}
    if ind == "atr":
        return "atr", {"period": rp.get("period", 14)}
    if ind == "atr_pct":
        # Reuse atr node, condition side carries metadata
        return "atr", {"period": rp.get("period", 14)}
    if ind == "volume":
        # No indicator node — read from ticker @volume
        return "volume", {}
    if ind == "price":
        # No indicator node — read @close from ticker
        return "price", {}
    # Generic / unknown (stochastic, adx, etc.)
    return ind, dict(rp)


def _needs_indicator_node(indicator_name: str) -> bool:
    """Return False for pseudo-indicators that have no dedicated node."""
    return indicator_name not in ("volume", "price")


def _indicator_attr(catalog_name: str) -> str:
    """Return the primary attribute written by an indicator catalog node."""
    _ATTR_MAP = {
        "rsi": "@rsi",
        "macd": "@macd_line",
        "sma": "@sma",
        "ema": "@ema",
        "bollinger": "@bb_upper",
        "atr": "@atr",
    }
    return _ATTR_MAP.get(catalog_name, f"@{catalog_name}")


def _ticker_attr_for(indicator_name: str, rule: Rule) -> str:
    """Return the ticker attribute that the comparison reads when there is no indicator node."""
    if indicator_name == "volume":
        return "@volume"
    # price or any other direct-from-ticker indicator
    return "@close"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _param_hash(params: dict[str, Any]) -> str:
    """Stable param hash for deduplication, e.g. 'period_14_type_sma'."""
    return "_".join(f"{k}_{v}" for k, v in sorted(params.items()))


def _indicator_path(catalog_name: str, params: dict[str, Any]) -> str:
    h = _param_hash(params)
    return f"/{catalog_name}_{h}" if h else f"/{catalog_name}"


def _wire_id(from_path: str, to_path: str, attr: Optional[str]) -> str:
    """Stable wire id: replace / with _, drop @ prefix."""
    f_slug = from_path.replace("/", "_").lstrip("_")
    t_slug = to_path.replace("/", "_").lstrip("_")
    a_slug = (attr or "").replace("@", "")
    return f"wire_{f_slug}_{t_slug}_{a_slug}"


# ---------------------------------------------------------------------------
# Param parsing helpers
# ---------------------------------------------------------------------------

_PARAM_MA_RE = re.compile(r"^ma:(\d+):(ema|sma)$")


def _parse_param_indicator(param: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Parse a rule.param string like 'ma:200:ema' into (catalog_name, params).

    Returns None if the param is not a cross-indicator reference.
    """
    m = _PARAM_MA_RE.match(param)
    if m:
        period = int(m.group(1))
        ma_type = m.group(2)
        node_name = "ema" if ma_type == "ema" else "sma"
        return node_name, {"period": period}
    # Fallback: try generic "signal" (handled at call site)
    return None


# ---------------------------------------------------------------------------
# Condition name resolution
# ---------------------------------------------------------------------------

def _resolve_condition(condition: str) -> str:
    """Map RuleCondition → catalog node name."""
    _COND_MAP = {
        "above": "above",
        "below": "below",
        "crossover_up": "crosses_above",
        "crosses_above": "crosses_above",
        "crossover_down": "crosses_below",
        "crosses_below": "crosses_below",
        "is_above_signal": "above",
        "is_below_signal": "below",
    }
    return _COND_MAP.get(condition, condition)


# ---------------------------------------------------------------------------
# Core builder state
# ---------------------------------------------------------------------------

class _GraphBuilder:
    """Accumulates nodes and wires during the auto_render traversal."""

    def __init__(self, ticker: str, interval: str, source: str) -> None:
        self.ticker = ticker
        self.interval = interval
        self.source = source

        self.nodes: dict[str, Node] = {}
        self.wires: list[Wire] = []

        # Deduplication: (catalog_name, params_hash) -> node_path
        self._indicator_cache: dict[tuple[str, str], str] = {}

        # Layout trackers per column
        self._col_y: dict[float, float] = {}

    def _next_y(self, col_x: float) -> float:
        y = self._col_y.get(col_x, 0.0)
        self._col_y[col_x] = y + _ROW_PITCH
        return y

    def _set_y(self, col_x: float, y: float) -> None:
        """Force the next-y cursor to at least *y* for the column."""
        current = self._col_y.get(col_x, 0.0)
        if y >= current:
            self._col_y[col_x] = y

    def _add_node(self, path: str, node_type: str, params: dict[str, Any],
                  position: tuple[float, float]) -> None:
        """Add a node; silently skip if path already registered (idempotent)."""
        if path in self.nodes:
            return
        self.nodes[path] = Node(id=path, type=node_type, params=params, position=position)

    def _add_wire(self, from_path: str, to_path: str, attr: Optional[str] = None) -> None:
        wire = Wire(**{
            "id": _wire_id(from_path, to_path, attr),
            "from": from_path,
            "to": to_path,
            "attr": attr,
        })
        # Deduplicate wires by content (same from/to/attr)
        key = (from_path, to_path, attr)
        for w in self.wires:
            if (w.from_path, w.to_path, w.attr) == key:
                return
        self.wires.append(wire)

    # ------------------------------------------------------------------
    # Ticker node
    # ------------------------------------------------------------------

    def add_ticker(self, symbol: str, interval: str, source: str,
                   prefix: str = "", y_offset: float = 0.0) -> str:
        sym_lower = symbol.lower().replace(".", "_")
        path = f"{prefix}/ticker_{sym_lower}_{interval}_{source}"
        y = y_offset
        self._add_node(path, "ticker", {
            "symbol": symbol,
            "interval": interval,
            "source": source,
        }, (_COL_TICKER, y))
        return path

    # ------------------------------------------------------------------
    # Indicator node (with dedup)
    # ------------------------------------------------------------------

    def add_indicator(self, catalog_name: str, params: dict[str, Any],
                      ticker_path: str, prefix: str = "",
                      y_offset: float = 0.0) -> str:
        cache_key = (catalog_name, _param_hash(params))
        if cache_key in self._indicator_cache:
            return self._indicator_cache[cache_key]

        path = prefix + _indicator_path(catalog_name, params)
        y = self._next_y(_COL_INDICATOR + (y_offset if y_offset else 0.0))
        self._add_node(path, catalog_name, params, (_COL_INDICATOR, y))
        self._indicator_cache[cache_key] = path

        # Wire ticker → indicator (ATR needs high/low/close; others just close)
        if catalog_name == "atr":
            self._add_wire(ticker_path, path, "@high")
            self._add_wire(ticker_path, path, "@low")
            self._add_wire(ticker_path, path, "@close")
        else:
            self._add_wire(ticker_path, path, "@close")

        return path

    # ------------------------------------------------------------------
    # Rule set → comparison + logic
    # ------------------------------------------------------------------

    def _emit_rule_set(
        self,
        side: str,
        rules: list[Rule],
        logic_op: str,
        ticker_path: str,
        prefix: str = "",
        y_base: float = 0.0,
    ) -> Optional[str]:
        """Emit indicator, comparison, NOT, and logic nodes for *rules*.

        Returns the logic node path (or None if rules is empty).
        """
        if not rules:
            return None

        logic_path = f"{prefix}/logic_{side}"
        logic_y = y_base + _ROW_PITCH * (len(rules) / 2.0)
        self._add_node(
            logic_path,
            logic_op.lower(),
            {},
            (_COL_LOGIC, logic_y),
        )

        for idx, raw_rule in enumerate(rules):
            rule = migrate_rule(raw_rule)
            row_y = y_base + idx * _ROW_PITCH

            # --- Indicator node ------------------------------------------
            catalog_name, ind_params = _resolve_indicator(rule)

            if _needs_indicator_node(catalog_name):
                ind_path = self.add_indicator(
                    catalog_name, ind_params, ticker_path, prefix=prefix
                )
                left_attr = _indicator_attr(catalog_name)
                left_src_path = ind_path
            else:
                # volume / price — reads directly from ticker
                left_attr = _ticker_attr_for(catalog_name, rule)
                left_src_path = ticker_path

            # --- Comparison node -----------------------------------------
            cmp_type = _resolve_condition(rule.condition)
            cmp_path = f"{prefix}/cmp_{side}_{idx}"
            cmp_params: dict[str, Any] = {}

            # Determine right-side input
            right_src_path: Optional[str] = None
            right_attr: Optional[str] = None

            if rule.param == "signal" or rule.condition in ("is_above_signal", "is_below_signal"):
                # MACD-signal comparison: left=@macd_line, right=@macd_signal
                left_attr = "@macd_line"
                right_attr = "@macd_signal"
                right_src_path = ind_path if _needs_indicator_node(catalog_name) else ticker_path
            elif rule.param and _parse_param_indicator(rule.param) is not None:
                parsed = _parse_param_indicator(rule.param)
                assert parsed is not None
                param_cat, param_params = parsed
                right_src_path = self.add_indicator(
                    param_cat, param_params, ticker_path, prefix=prefix
                )
                right_attr = _indicator_attr(param_cat)
            elif rule.value is not None and rule.param is None:
                # indicator-vs-scalar
                cmp_params["threshold"] = rule.value
                right_src_path = None
            else:
                # fallback: store value/threshold in params
                if rule.value is not None:
                    cmp_params["threshold"] = rule.value
                if rule.threshold is not None:
                    cmp_params["min_move_pct"] = rule.threshold

            if rule.condition in ("atr_pct",) or catalog_name == "atr_pct":
                cmp_params["condition_extra"] = "atr_pct"

            self._add_node(cmp_path, cmp_type, cmp_params, (_COL_COMPARISON, row_y))

            # Wire left input → comparison
            self._add_wire(left_src_path, cmp_path, left_attr)

            # Wire right input → comparison (if two-input)
            if right_src_path is not None and right_attr is not None:
                self._add_wire(right_src_path, cmp_path, right_attr)

            # --- NOT wrapper (if negated) --------------------------------
            if rule.negated:
                not_path = f"{prefix}/not_{side}_{idx}"
                self._add_node(not_path, "not", {}, (_COL_COMPARISON, row_y + _ROW_PITCH * 0.5))
                self._add_wire(cmp_path, not_path, "@bool")
                logic_input_path = not_path
            else:
                logic_input_path = cmp_path

            # Wire comparison/NOT → logic
            self._add_wire(logic_input_path, logic_path, "@bool")

        return logic_path

    # ------------------------------------------------------------------
    # Regime sub-tree
    # ------------------------------------------------------------------

    def add_regime(
        self,
        regime: RegimeConfig,
        ticker: str,
        source: str,
        ticker_path: str,
        buy_logic_path: Optional[str],
        sell_logic_path: Optional[str],
        entry_path: str,
        exit_path: str,
    ) -> None:
        """Emit regime sub-tree and gate buy/sell into entry/exit via AND nodes."""
        prefix = "/regime"
        y_off = _REGIME_Y_OFFSET

        # Regime ticker (same symbol, different timeframe)
        regime_ticker = self.add_ticker(ticker, regime.timeframe, source, prefix=prefix, y_offset=y_off)

        if regime.rules:
            # Full rule set path
            regime_logic_path = self._emit_rule_set(
                "regime",
                list(regime.rules),
                regime.logic,
                regime_ticker,
                prefix=prefix,
                y_base=y_off,
            )
        else:
            # Legacy single-indicator regime
            ind_name, ind_params = _resolve_indicator_from_regime(regime)
            regime_ind_path = self.add_indicator(
                ind_name, ind_params, regime_ticker, prefix=prefix
            )
            # Single comparison
            regime_cmp_path = f"{prefix}/cmp_regime_0"
            regime_cmp_type = _resolve_condition(regime.condition)
            regime_cmp_params: dict[str, Any] = {}
            self._add_node(regime_cmp_path, regime_cmp_type, regime_cmp_params, (_COL_COMPARISON, y_off))
            self._add_wire(regime_ind_path, regime_cmp_path, _indicator_attr(ind_name))

            regime_logic_path = f"{prefix}/logic_regime"
            self._add_node(regime_logic_path, "and", {}, (_COL_LOGIC, y_off))
            self._add_wire(regime_cmp_path, regime_logic_path, "@bool")

        if regime_logic_path is None:
            return

        # Gate buy side
        if buy_logic_path is not None:
            gate_buy = "/and_regime_buy_gate"
            self._add_node(gate_buy, "and", {}, (_COL_REGIME_GATE, 0.0))
            self._add_wire(regime_logic_path, gate_buy, "@bool")
            self._add_wire(buy_logic_path, gate_buy, "@bool")
            self._add_wire(gate_buy, entry_path, "@bool")
        else:
            # No buy logic — wire regime directly to entry
            self._add_wire(regime_logic_path, entry_path, "@bool")

        # Gate sell side
        if sell_logic_path is not None:
            gate_sell = "/and_regime_sell_gate"
            self._add_node(gate_sell, "and", {}, (_COL_REGIME_GATE, _ROW_PITCH))
            self._add_wire(regime_logic_path, gate_sell, "@bool")
            self._add_wire(sell_logic_path, gate_sell, "@bool")
            self._add_wire(gate_sell, exit_path, "@bool")
        else:
            self._add_wire(regime_logic_path, exit_path, "@bool")

    # ------------------------------------------------------------------
    # Settings nodes
    # ------------------------------------------------------------------

    def add_settings(self, req: StrategyRequest) -> None:
        y = _SETTINGS_Y_START
        b23_mode = _is_b23_mode(req)

        self._add_node(
            "/setting_position_size",
            "position_size",
            {"size": req.position_size},
            (_SETTINGS_X, y),
        )
        y += _SETTINGS_Y_PITCH

        # Stop loss — simple mode
        if not b23_mode:
            if req.stop_loss_pct is not None:
                self._add_node(
                    "/setting_stop_loss",
                    "stop_loss",
                    {"pct": req.stop_loss_pct},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH
        else:
            # Per-direction stop loss nodes
            if req.long_stop_loss_pct is not None:
                self._add_node(
                    "/setting_long_stop_loss",
                    "stop_loss",
                    {"pct": req.long_stop_loss_pct, "direction": "long"},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH
            if req.short_stop_loss_pct is not None:
                self._add_node(
                    "/setting_short_stop_loss",
                    "stop_loss",
                    {"pct": req.short_stop_loss_pct, "direction": "short"},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH

        self._add_node(
            "/setting_slippage",
            "slippage",
            {"bps": req.slippage_bps},
            (_SETTINGS_X, y),
        )
        y += _SETTINGS_Y_PITCH

        self._add_node(
            "/setting_commission",
            "commission",
            {"per_share_rate": req.per_share_rate, "min_per_order": req.min_per_order},
            (_SETTINGS_X, y),
        )
        y += _SETTINGS_Y_PITCH

        # Trailing stop (generic node, not in Core 14 — viewer falls back)
        if not b23_mode and req.trailing_stop is not None:
            self._add_node(
                "/setting_trailing_stop",
                "trailing_stop",
                req.trailing_stop.model_dump(),
                (_SETTINGS_X, y),
            )
        elif b23_mode:
            if req.long_trailing_stop is not None:
                self._add_node(
                    "/setting_long_trailing_stop",
                    "trailing_stop",
                    {**req.long_trailing_stop.model_dump(), "direction": "long"},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH
            if req.short_trailing_stop is not None:
                self._add_node(
                    "/setting_short_trailing_stop",
                    "trailing_stop",
                    {**req.short_trailing_stop.model_dump(), "direction": "short"},
                    (_SETTINGS_X, y),
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_b23_mode(req: StrategyRequest) -> bool:
    """True if any per-direction rule list is populated (b23 mode)."""
    return any([
        req.long_buy_rules,
        req.long_sell_rules,
        req.short_buy_rules,
        req.short_sell_rules,
    ])


def _resolve_indicator_from_regime(regime: RegimeConfig) -> tuple[str, dict[str, Any]]:
    """Resolve a single-indicator regime to (catalog_name, params)."""
    ind = regime.indicator
    ip = regime.indicator_params or {}
    if ind == "ma":
        ma_type = ip.get("type", "sma")
        node_name = "ema" if ma_type == "ema" else "sma"
        return node_name, {"period": ip.get("period", 200)}
    if ind == "rsi":
        return "rsi", {"period": ip.get("period", 14), "type": ip.get("type", "sma")}
    if ind == "macd":
        return "macd", {
            "fast": ip.get("fast", 12),
            "slow": ip.get("slow", 26),
            "signal": ip.get("signal", 9),
        }
    if ind == "bb":
        return "bollinger", {"period": ip.get("period", 20), "stddev": ip.get("stddev", 2.0)}
    if ind == "atr":
        return "atr", {"period": ip.get("period", 14)}
    return ind, dict(ip)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def auto_render(req: StrategyRequest) -> Graph:
    """Translate a StrategyRequest into a readOnly Graph for the T1 viewer.

    The returned graph has:
      - readOnly=True
      - _version=1
      - Deterministic node paths for stable test snapshots
      - No dangling wires (Pydantic model_validator enforces this)
      - No cycles (Pydantic model_validator enforces this)
    """
    ticker = req.ticker
    interval = req.interval
    source = req.source
    b23_mode = _is_b23_mode(req)
    has_regime = req.regime is not None and req.regime.enabled

    builder = _GraphBuilder(ticker, interval, source)

    # Primary ticker node
    sym_lower = ticker.lower().replace(".", "_")
    ticker_path = f"/ticker_{sym_lower}_{interval}_{source}"
    builder._add_node(ticker_path, "ticker", {
        "symbol": ticker,
        "interval": interval,
        "source": source,
    }, (_COL_TICKER, 0.0))

    # Entry / Exit terminal nodes
    entry_path = "/entry"
    exit_path = "/exit"
    # Entry at col 5 if regime (gate nodes in col 4), else col 4
    terminal_x = _COL_TERMINAL if has_regime else _COL_REGIME_GATE
    builder._add_node(entry_path, "entry", {}, (terminal_x, 0.0))
    builder._add_node(exit_path, "exit", {}, (terminal_x, _ROW_PITCH))

    # Settings nodes (always)
    builder.add_settings(req)

    # --- Rule set emission -----------------------------------------------

    if not b23_mode:
        # Simple mode: buy_rules + sell_rules
        buy_logic_path = builder._emit_rule_set(
            "buy", list(req.buy_rules), req.buy_logic, ticker_path, y_base=0.0
        )
        sell_logic_path = builder._emit_rule_set(
            "sell", list(req.sell_rules), req.sell_logic, ticker_path,
            y_base=len(req.buy_rules) * _ROW_PITCH
        )

        if not has_regime:
            # Direct wire: logic → terminals
            if buy_logic_path:
                builder._add_wire(buy_logic_path, entry_path, "@bool")
            if sell_logic_path:
                builder._add_wire(sell_logic_path, exit_path, "@bool")

    else:
        # B23 mode: per-direction rule sets
        long_buy_logic = builder._emit_rule_set(
            "long_buy",
            list(req.long_buy_rules or []),
            req.long_buy_logic,
            ticker_path,
            y_base=0.0,
        )
        long_sell_logic = builder._emit_rule_set(
            "long_sell",
            list(req.long_sell_rules or []),
            req.long_sell_logic,
            ticker_path,
            y_base=len(req.long_buy_rules or []) * _ROW_PITCH,
        )
        short_buy_logic = builder._emit_rule_set(
            "short_buy",
            list(req.short_buy_rules or []),
            req.short_buy_logic,
            ticker_path,
            y_base=(len(req.long_buy_rules or []) + len(req.long_sell_rules or [])) * _ROW_PITCH,
        )
        short_sell_logic = builder._emit_rule_set(
            "short_sell",
            list(req.short_sell_rules or []),
            req.short_sell_logic,
            ticker_path,
            y_base=(
                len(req.long_buy_rules or [])
                + len(req.long_sell_rules or [])
                + len(req.short_buy_rules or [])
            ) * _ROW_PITCH,
        )

        # Combine per-direction logic into a single OR for entry/exit
        # Long and short buy → OR → entry
        if long_buy_logic and short_buy_logic:
            or_buy = "/or_b23_buy"
            builder._add_node(or_buy, "or", {}, (_COL_LOGIC + 100, 0.0))
            builder._add_wire(long_buy_logic, or_buy, "@bool")
            builder._add_wire(short_buy_logic, or_buy, "@bool")
            buy_logic_path_combined: Optional[str] = or_buy
        elif long_buy_logic:
            buy_logic_path_combined = long_buy_logic
        elif short_buy_logic:
            buy_logic_path_combined = short_buy_logic
        else:
            buy_logic_path_combined = None

        if long_sell_logic and short_sell_logic:
            or_sell = "/or_b23_sell"
            builder._add_node(or_sell, "or", {}, (_COL_LOGIC + 100, _ROW_PITCH))
            builder._add_wire(long_sell_logic, or_sell, "@bool")
            builder._add_wire(short_sell_logic, or_sell, "@bool")
            sell_logic_path_combined: Optional[str] = or_sell
        elif long_sell_logic:
            sell_logic_path_combined = long_sell_logic
        elif short_sell_logic:
            sell_logic_path_combined = short_sell_logic
        else:
            sell_logic_path_combined = None

        # Store for regime gating
        buy_logic_path = buy_logic_path_combined
        sell_logic_path = sell_logic_path_combined

        if not has_regime:
            if buy_logic_path:
                builder._add_wire(buy_logic_path, entry_path, "@bool")
            if sell_logic_path:
                builder._add_wire(sell_logic_path, exit_path, "@bool")

    # --- Regime sub-tree -------------------------------------------------

    if has_regime:
        assert req.regime is not None
        builder.add_regime(
            req.regime,
            ticker,
            source,
            ticker_path,
            buy_logic_path if not b23_mode else buy_logic_path,
            sell_logic_path if not b23_mode else sell_logic_path,
            entry_path,
            exit_path,
        )

    return Graph.model_validate({
        "_version": 1,
        "readOnly": True,
        "nodes": {path: node.model_dump(by_alias=False) for path, node in builder.nodes.items()},
        "wires": [w.model_dump(by_alias=False) for w in builder.wires],
    })
