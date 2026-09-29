"""Turn a graph's Settings nodes into simulator field values.

compile() reads Settings nodes (Position Size, Stop Loss, Slippage,
Commission, Trailing Stop) into a list of SimulatorSetting(key, value).  This module is the
one place that says which request field each key fills.  The graph backtest
uses it now; the bot runner uses the same function, so a live graph bot
trades with the same size, stop and costs that its backtest used.

The graph wins: a value set by a Settings node replaces the value from the
request or bot config.
"""
from __future__ import annotations

from typing import Any, Iterable

# SimulatorSetting.key -> field name on StrategyRequest / GraphBacktestRequest.
# BotConfig uses the same names for position_size, stop_loss_pct,
# slippage_bps and trailing_stop.  It has no commission fields, so a bot
# caller skips those.
SETTING_FIELDS: dict[str, str] = {
    "position_size": "position_size",
    "stop_loss": "stop_loss_pct",
    "slippage_bps": "slippage_bps",
    "per_share_rate": "per_share_rate",
    "min_per_order": "min_per_order",
    "trailing_stop": "trailing_stop",
}

# Fields whose value is passed through as it is (a TrailingStopConfig),
# not turned into a float.
OBJECT_FIELDS: frozenset[str] = frozenset({"trailing_stop"})

# Fields a graph can own through a Settings node.
GRAPH_OWNED_FIELDS: frozenset[str] = frozenset(SETTING_FIELDS.values())

# Per-direction fields that take priority over the plain field when set
# (the bot runner reads long_/short_stop_loss_pct before stop_loss_pct).
# A caller that applies a graph's stop or size must also set or clear these,
# or the old per-direction value quietly wins over the graph.
DIRECTION_SHADOWS: dict[str, tuple[str, str]] = {
    "stop_loss_pct": ("long_stop_loss_pct", "short_stop_loss_pct"),
    "position_size": ("long_position_size", "short_position_size"),
    "trailing_stop": ("long_trailing_stop", "short_trailing_stop"),
}


def settings_overrides(simulator_settings: Iterable) -> dict[str, Any]:
    """Return {field_name: value} for every Settings node in the graph.

    Numbers come back as floats.  trailing_stop comes back as the
    TrailingStopConfig that compile built.  Settings are applied in compile
    order, so if two nodes set the same field, the later one wins.  Keys this
    module does not know are ignored.
    """
    out: dict[str, Any] = {}
    for setting in simulator_settings:
        field = SETTING_FIELDS.get(setting.key)
        if field is None:
            continue
        out[field] = setting.value if field in OBJECT_FIELDS else float(setting.value)
    return out


# Per-direction fields a graph bot always clears.  The graph backtest runs
# with b23_mode off, so it reads only the plain fields (stop_loss_pct,
# position_size, trailing_stop, max_bars_held) for both sides.  The bot
# runner prefers a per-direction value over the plain one when it is set, so
# a leftover per-direction value would trade differently from the backtest.
# Clearing them all keeps live equal to backtest, whether or not the graph
# has a Settings node for that field.
BOT_DIRECTION_FIELDS: tuple[str, ...] = (
    *DIRECTION_SHADOWS["stop_loss_pct"],
    *DIRECTION_SHADOWS["position_size"],
    *DIRECTION_SHADOWS["trailing_stop"],
    "long_max_bars_held",
    "short_max_bars_held",
)


def apply_to_bot_config(cfg, simulator_settings: Iterable):
    """Return a copy of a graph bot's config with the graph's settings applied.

    The graph wins: a Settings node's value replaces the bot config's value.
    Fields the bot config does not have (the commission keys) are skipped,
    and every per-direction field is cleared (see BOT_DIRECTION_FIELDS).

    Position size: a Position Size node's `size` is a fraction of capital,
    where 1.0 means 100%.  The graph backtest puts it on a StrategyRequest,
    whose validator clamps it into [0.01, 1.0], so a size of 100 runs as 1.0
    (full size, not 100 times).  model_validate runs BotConfig's same clamp,
    so a live bot sizes 100 as 1.0 too, and 1 as 1.0.  Never build this with
    model_copy(update=...): it skips the clamp, and a size of 100 would buy
    100 times the capital.

    The copy is built without re-validating the graph itself: the graph
    object is passed through as it is.
    """
    cls = type(cfg)
    fields = cls.model_fields
    overrides = {
        name: value
        for name, value in settings_overrides(simulator_settings).items()
        if name in fields
    }
    for name in BOT_DIRECTION_FIELDS:
        if name in fields:
            overrides[name] = None
    data = cfg.model_dump(exclude={"graph"})
    data.update(overrides)
    if "graph" in fields:
        data["graph"] = cfg.graph
    return cls.model_validate(data)
