"""Graph backtest core.

run_graph_backtest() compiles a graph, computes its indicators and runs the
shared simulator.  It lives here, not in the routes module, so the API route
and the bot code can both call it.  The route in routes/nodebuilder.py is a
thin wrapper that turns errors into HTTP responses.

The graph cooks once per frame, before the simulations: every node runs
over the whole frame, and each Output Group's simulation reads its terminal
columns bar by bar (nodebuilder.trading.sim_bridge; one simulation per
group, plan D7).  Groups are combined by nodes_groups.combine.  The
editor's backtest keeps every node's stream from that cook (keep_all) so
the wire inspector can read it from the cook cache (plan D6) without
cooking again; the bot path keeps only the terminals.  This module never
imports the cook cache: the route does.

_apply_settings_overrides, _settings_to_strategy_request and
_make_cached_eval are the Wave 0 to 4 helpers.  The backtest no longer
uses them; they stay for the tests that import them.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd

from models import StrategyRequest
from nodebuilder import evaluator as _evaluator
from nodebuilder.api_models import (
    GraphBacktestGroupedResponse,
    GraphBacktestRequest,
    GraphBacktestResponse,
    GraphCombinedResult,
    GraphGroupResult,
)
from nodebuilder.compile import compile as _compile_graph
from nodebuilder.evaluator import evaluate_graph
from nodebuilder.prepare import NO_EXIT_ATTR, build_graph_attrs
from nodebuilder.sim_settings import settings_overrides
from nodebuilder.trading import nodes_groups as _groups
from nodebuilder.trading import sim_bridge as _sb

# The exit sentinel compile() uses when nothing is wired into Exit.
_NO_EXIT_ATTR = NO_EXIT_ATTR

# Summary keys only the graph backtest returns.  The rule backtest response
# does not have them, so parity checks skip these keys.
GRAPH_ONLY_SUMMARY_KEYS: frozenset[str] = frozenset({"open_position", "exit_connected"})


def _apply_settings_overrides(req: GraphBacktestRequest, simulator_settings: list) -> dict:
    """Build a dict of simulator fields, applying graph SimulatorSettings as overrides.

    Legacy (Waves 0 to 4): the backtest now takes its fields from each
    group's plan (sim_bridge.strategy_fields).

    The GRAPH WINS: if the same field is specified in both the request and the graph,
    the graph's compile-time SimulatorSetting takes precedence.  This reflects the
    design intent: the graph is the authoritative specification at backtest time.
    The key mapping lives in nodebuilder/sim_settings.py, shared with the bot runner.
    """
    overrides: dict = {
        "initial_capital": req.initial_capital,
        "position_size": req.position_size,
        "stop_loss_pct": req.stop_loss_pct,
        "trailing_stop": req.trailing_stop,
        "max_bars_held": req.max_bars_held,
        "slippage_bps": req.slippage_bps,
        "commission_pct": req.commission_pct,
        "per_share_rate": req.per_share_rate,
        "min_per_order": req.min_per_order,
        "borrow_rate_annual": req.borrow_rate_annual,
        "dynamic_sizing": req.dynamic_sizing,
        "skip_after_stop": req.skip_after_stop,
        "trading_hours": req.trading_hours,
        "direction": req.direction,
    }
    overrides.update(settings_overrides(simulator_settings))
    return overrides


def _settings_to_strategy_request(settings: dict, req: GraphBacktestRequest) -> StrategyRequest:
    """Build a minimal StrategyRequest from the GraphBacktestRequest + settings dict.

    The buy_rules / sell_rules are empty — _run_simulation reads simulator-level
    fields, not rules.  b23_mode is False (graph mode never uses the dual-rule path).
    """
    return StrategyRequest(
        ticker=req.ticker,
        start=req.start,
        end=req.end,
        interval=req.interval,
        source=req.source,
        buy_rules=[],
        sell_rules=[],
        initial_capital=settings["initial_capital"],
        position_size=settings["position_size"],
        stop_loss_pct=settings["stop_loss_pct"],
        trailing_stop=settings["trailing_stop"],
        max_bars_held=settings["max_bars_held"],
        slippage_bps=settings["slippage_bps"],
        commission_pct=settings["commission_pct"],
        per_share_rate=settings["per_share_rate"],
        min_per_order=settings["min_per_order"],
        borrow_rate_annual=settings["borrow_rate_annual"],
        dynamic_sizing=settings["dynamic_sizing"],
        skip_after_stop=settings["skip_after_stop"],
        trading_hours=settings["trading_hours"],
        direction=settings["direction"],
    )


def _make_cached_eval(program, attrs):
    """Return a callable that evaluates the graph at bar i, memoised per bar.

    evaluate_graph mutates attrs in-place.  Calling it twice per bar (once for
    the buy fn and once for the sell fn) is safe — the second call overwrites
    with identical values — but is wasteful.  The cache avoids the double call.
    """
    cache: dict[int, dict] = {}

    def call(i: int) -> dict:
        if i not in cache:
            cache[i] = evaluate_graph(program, attrs, i)
        return cache[i]

    return call


def _build_baseline_curve(df: pd.DataFrame, initial_capital: float, date_strs: list) -> list[dict]:
    """Buy-and-hold baseline: initial_capital * close[i] / close[0]."""
    close_arr = df["Close"].to_numpy(dtype=float, copy=False)
    first_close = float(close_arr[0])
    return [
        {"time": date_strs[i], "value": round(initial_capital * close_arr[i] / first_close, 2)}
        for i in range(len(df))
    ]


def _open_position(trades: list[dict], last_close: float) -> dict | None:
    """Describe the position still open at the last bar, or None if flat.

    The simulator counts only closed trades in num_trades, but final_value
    includes an open position at the last close.  This tells the UI why the
    return can be non-zero with zero trades.
    """
    if not trades or trades[-1].get("type") not in ("buy", "short"):
        return None
    entry = trades[-1]
    direction = entry.get("direction") or ("short" if entry["type"] == "short" else "long")
    entry_price = float(entry["price"])
    if entry_price <= 0:
        unrealized_pct = 0.0
    elif direction == "short":
        unrealized_pct = (entry_price - last_close) / entry_price * 100
    else:
        unrealized_pct = (last_close - entry_price) / entry_price * 100
    return {
        "direction": direction,
        "entry_price": round(entry_price, 4),
        "unrealized_pct": round(unrealized_pct, 2),
    }


@dataclass
class GraphCook:
    """One cook of a graph over one fetched frame.

    program : the CompiledProgram.
    result  : the kernel CookResult (every node's stream when keep_all).
    df      : the window's main frame.
    frames  : ``((symbol, interval, df), ...)`` for EVERY frame the cook
              read: the main frame first, then each reference Ticker's
              frame (plan D8).  The editor's cache key fingerprints all of
              them (plan D6).
    """
    program: Any
    result: Any
    df: pd.DataFrame
    frames: tuple = ()


def cook_attrs(program, attrs: dict, *, keep_all: bool = False):
    """Cook *program* over the bars in *attrs* (as build_graph_attrs makes
    them, reference frames included) and return the kernel CookResult.

    The bars are read exactly as evaluate_graph reads them, so a backtest
    cooked here gives the signals the per-bar adapter gave.  keep_all keeps
    every node's output stream (the editor's inspector cook); otherwise
    only the terminals' streams are kept.  CPU work: never call this on an
    event loop.
    """
    return _evaluator.cook_attrs(program, attrs, keep_all=keep_all)


def fetch_reference_frames(
    program,
    start: str,
    end: str,
    source: str,
    given: Optional[Mapping[tuple[str, str], pd.DataFrame]] = None,
) -> dict[tuple[str, str], pd.DataFrame]:
    """The frames of the reference Tickers *program* reads (plan D8), by
    (SYMBOL, interval).

    A frame in *given* (keyed the same way) is used as it is; every other
    one is fetched through the TTL-cached ``shared._fetch`` with *source*,
    from start minus its lookback padding (prepare.reference_fetches) to
    *end*.  A reference never reuses the primary's frame, which starts at
    *start* with no padding.  Raises ValueError when a fetch returns no
    bars.  I/O: never call this on an event loop.
    """
    from nodebuilder.prepare import reference_fetches
    from shared import _fetch

    have = {_frame_key(sym, itv): df for (sym, itv), df in (given or {}).items()}
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for f in reference_fetches(program, start, end):
        df = have.get(f.key)
        if df is None:
            df = _fetch(f.symbol, f.start, f.end, f.interval, source=source)
        if df is None or len(df) == 0:
            raise ValueError(
                f"No data for reference ticker {f.symbol} in {f.start}..{f.end} ({f.interval}).")
        out[f.key] = df
    return out


def cook_graph_window(
    graph,
    *,
    ticker: str,
    start: str,
    end: str,
    interval: str,
    source: str,
    df: Optional[pd.DataFrame] = None,
    refs: Optional[Mapping[tuple[str, str], pd.DataFrame]] = None,
) -> GraphCook:
    """Compile *graph*, fetch its frame (unless *df* is given) and cook it
    with every node's stream kept.  The same cook a backtest makes, without
    the simulation: what the inspector does on a cache miss (plan D6).

    *refs* are reference frames already fetched (fetch_reference_frames'
    output); the route passes the ones its cache key was built from, so the
    cook reads exactly the frames it is stored under.

    Raises GraphValidationError for a graph that does not compile, and
    ValueError or HTTPException(400) for a bad source or missing data.
    """
    from shared import _fetch, require_valid_source

    src = require_valid_source(source)
    program = window_program(_compile_graph(graph), ticker, interval)
    if df is None:
        df = _fetch(ticker, start, end, interval, source=src)
    if df is None or len(df) == 0:
        raise ValueError(f"No data for {ticker} in {start}..{end} ({interval}).")
    refs = fetch_reference_frames(program, start, end, src, given=refs)
    attrs = build_graph_attrs(program, df, frames=refs, interval=interval)
    return GraphCook(program=program, result=cook_attrs(program, attrs, keep_all=True), df=df,
                     frames=((ticker, interval, df),) + _ref_frames_list(refs))


def cook_program(program, groups, *, with_unclaimed: bool = True):
    """*program* cut to what one cook of *groups* runs (KA-2, KA-4).

    - Every step the groups' terminals depend on (their step_ids).
    - with_unclaimed: also each step no group depends on (a node the user
      is still wiring, shown by the inspector), with its inputs, but only
      when every Ticker it reads is one some group reads, or one that reads
      the cook's own frame.  A stray Ticker no group reads (just dropped,
      or a half-typed symbol) is never fetched, and the nodes below it are
      left out of the cook (skipped), so it can never fail a run.

    Returns *program* itself when nothing is cut, so a graph with no stray
    nodes cooks exactly as before.
    """
    from nodebuilder.trading.align import ticker_roles
    from nodebuilder.trading.nodes_data import TICKER_TYPE

    all_groups = tuple(program.groups or ())
    claimed: set[str] = set()
    for g in all_groups:
        claimed |= set(g.step_ids)
    ids: set[str] = set()
    for g in groups:
        ids |= set(g.step_ids)
    if with_unclaimed:
        by_id = {s.node_id: s for s in program.steps}
        safe = {nid for nid in claimed
                if by_id.get(nid) is not None and by_id[nid].type == TICKER_TYPE}
        safe |= set(ticker_roles(program).primary)
        tainted: set[str] = set()
        kept: list[str] = []
        for st in program.steps:     # topological order
            bad = (st.type == TICKER_TYPE and st.mode == "run" and st.node_id not in safe) \
                or any(u in tainted for u in st.depends_on())
            if bad:
                tainted.add(st.node_id)
            elif st.node_id not in claimed:
                kept.append(st.node_id)
        ids |= set(_groups._upstream(program, kept))
    if len(ids) == len(program.steps) and all(s.node_id in ids for s in program.steps):
        return program
    return _groups.program_for_steps(program, ids)


def window_program(program, ticker: str, interval: str):
    """*program* cut to the groups that trade the window's frame (ticker,
    interval), plus the unclaimed nodes cook_program keeps: what the
    inspector cooks over that frame (KA-2).  A group on another symbol or
    interval is left out, so its nodes are never cooked on another symbol's
    bars."""
    key = _frame_key(ticker, interval)
    groups = [g for g in (program.groups or ())
              if _frame_key(*g.resolve(ticker, interval, "long")[:2]) == key]
    return cook_program(program, groups)


def _ref_frames_list(refs: Mapping[tuple[str, str], pd.DataFrame]) -> tuple:
    """GraphCook.frames entries for the reference frames a cook read."""
    return tuple((sym, itv, df) for (sym, itv), df in refs.items())


def run_graph_backtest(
    req: GraphBacktestRequest,
    df: pd.DataFrame | None = None,
    *,
    frames: Optional[Mapping[tuple[str, str], pd.DataFrame]] = None,
) -> GraphBacktestResponse:
    """Core graph backtest logic — callable from the route, bot code and tests.

    Args:
        req: GraphBacktestRequest with graph + simulator settings.
        df: Optional pre-fetched DataFrame for the request's ticker and
            interval (bypasses _fetch; used in parity tests).
        frames: Optional pre-fetched frames by (symbol, interval), for
            graphs whose Output Groups trade other symbols.

    Returns the four-key GraphBacktestResponse (summary, trades,
    equity_curve, baseline_curve).  With one group these are that group's
    results; with more they are the combined results (see
    run_graph_backtest_cooked for the per-group results).

    Raises:
        GraphValidationError (and subclasses): the graph cannot be compiled or run.
            Each carries node_id naming the node at fault, or None.
        ValueError: invalid source or other data issues.
        HTTPException: re-raised from _run_simulation.
    """
    response, _cook = run_graph_backtest_cooked(req, df, keep_all=False, frames=frames)
    return GraphBacktestResponse.model_construct(
        summary=response.summary, trades=response.trades,
        equity_curve=response.equity_curve, baseline_curve=response.baseline_curve,
    )


@dataclass
class _Leg:
    """One group's run inside a backtest."""
    group: Any            # nodes_groups.GroupProgram
    symbol: str
    interval: str
    direction: str
    capital: float
    plan: Any             # sim_bridge.GroupPlan
    fields: dict
    frame: tuple = ()     # (SYMBOL, interval): the frame key
    df: Optional[pd.DataFrame] = None
    date_strs: Optional[list] = None
    sim: Optional[dict] = None
    summary: Optional[dict] = None
    baseline: Optional[list] = None


def _frame_key(symbol: str, interval: str) -> tuple[str, str]:
    return (str(symbol).strip().upper(), str(interval))


def plan_legs(program, req) -> list[_Leg]:
    """One _Leg per group of *program*: the symbol, interval, direction,
    capital and simulator fields it runs with.

    The implicit group "main" takes the request's ticker, interval and
    direction.  Capital is split by capital_weight (plan D7); a graph with
    one group gets the whole initial capital.  A group with capital weight
    0 gets no leg: it is not simulated and is left out of the combined
    result (S32a; compile warns group_weight_zero).
    """
    groups = tuple(g for g in (program.groups or ()) if g.weight > 0)
    if not groups:
        raise ValueError("compiled program has no group with a capital weight above 0")
    base = _sb.request_fields(req)
    total = sum(g.weight for g in groups)
    legs = []
    for g in groups:
        symbol, interval, _direction = g.resolve(req.ticker, req.interval, req.direction)
        plan = g.plan_for(req.direction)
        capital = (float(req.initial_capital) if len(groups) == 1
                   else float(req.initial_capital) * g.weight / total)
        fields = _sb.strategy_fields(base, plan)
        fields["initial_capital"] = capital
        legs.append(_Leg(
            group=g, symbol=symbol, interval=interval, direction=plan.direction,
            capital=capital, plan=plan, fields=fields, frame=_frame_key(symbol, interval),
        ))
    return legs


def _frame_trailing(legs: list[_Leg]):
    """The trailing stop build_graph_attrs prepares the frame for: an ATR
    one when any group on the frame uses it (the frame then carries the
    ATR column), else the first group's."""
    for leg in legs:
        ts = _sb.atr_trailing(leg.fields)
        if getattr(ts, "type", None) == "atr":
            return ts
    return legs[0].fields.get("trailing_stop")


def run_graph_backtest_cooked(
    req: GraphBacktestRequest,
    df: pd.DataFrame | None = None,
    *,
    keep_all: bool = True,
    frames: Optional[Mapping[tuple[str, str], pd.DataFrame]] = None,
) -> tuple[GraphBacktestGroupedResponse, GraphCook]:
    """run_graph_backtest with the per-group results, plus the cook it made
    (for the editor's cook cache).  keep_all (the default here) keeps every
    node's stream so the wire inspector can read it; run_graph_backtest
    passes False.

    One simulation per Output Group (plan D7).  Groups on the same symbol
    and interval share one cook of the graph.  When the groups trade
    different frames, each frame cooks only the nodes its groups depend on
    (the first frame also cooks the nodes no group depends on), and the
    returned GraphCook is the first frame's cook.

    Reference Tickers (plan D8) read their own frames: from *frames* when
    the caller passes them (keyed (SYMBOL, interval)), else fetched with
    lookback padding (fetch_reference_frames); never the request's *df*.
    """
    from shared import _fetch, _format_time_index, require_valid_source

    # 1. Validate the source, compile, and lay out the groups.
    source = require_valid_source(req.source)
    program = _compile_graph(req.graph)
    legs = plan_legs(program, req)

    # 2. The frames: the pre-fetched ones first, then fetch the rest.
    have: dict[tuple[str, str], pd.DataFrame] = {}
    if df is not None:
        have[_frame_key(req.ticker, req.interval)] = df
    for (sym, itv), frame_df in (frames or {}).items():
        have.setdefault(_frame_key(sym, itv), frame_df)
    by_frame: dict[tuple[str, str], list[_Leg]] = {}
    for leg in legs:
        by_frame.setdefault(leg.frame, []).append(leg)

    first_cook: Optional[GraphCook] = None

    # 3. One cook per frame, one simulation per group.
    for n, (key, frame_legs) in enumerate(by_frame.items()):
        lead = frame_legs[0]
        frame_df = have.get(key)
        if frame_df is None:
            frame_df = _fetch(lead.symbol, req.start, req.end, lead.interval, source=source)
            have[key] = frame_df
        if frame_df is None or len(frame_df) == 0:
            raise ValueError(f"No data for {lead.symbol} in {req.start}..{req.end} ({lead.interval}).")

        # The frame's groups, plus (first frame only) the nodes no group
        # depends on whose Tickers some group reads (cook_program, KA-4).
        # A weight-0 group has no leg and is not cooked.
        frame_program = cook_program(program, [leg.group for leg in frame_legs],
                                     with_unclaimed=(n == 0))

        # The attrs the graph cooks from: the bar series, the always-false
        # exit sentinel, ATR(14) when a trailing stop is ATR based (without
        # it the ATR reads 0 and the trail fires at once), and the frames
        # of the reference Tickers the frame's groups read (plan D8; the
        # caller's *frames* first, else fetched with lookback padding).
        # nodebuilder.prepare builds them for the live bot too.
        refs = fetch_reference_frames(frame_program, req.start, req.end, source, given=frames)
        attrs = build_graph_attrs(frame_program, frame_df, _frame_trailing(frame_legs),
                                  frames=refs, interval=lead.interval)
        keep: set[str] = set()
        for leg in frame_legs:
            keep |= leg.plan.keep_ids()
        result = _sb.cook(frame_program, attrs, keep, keep_all=keep_all)
        date_strs = _format_time_index(frame_df.index, lead.interval)
        last_close = float(frame_df["Close"].iloc[-1])

        for leg in frame_legs:
            sim_req = _sb.to_strategy_request(leg.fields, req, ticker=leg.symbol,
                                              interval=leg.interval)
            sim = _sb.simulate(leg.plan, result, frame_df, attrs, sim_req, date_strs)
            # Graph-only summary fields: an open position at the end, and
            # whether anything is wired into Exit.
            summary = dict(sim["summary"])
            summary["open_position"] = _open_position(sim["trades"], last_close)
            summary["exit_connected"] = bool(leg.group.exit_connected)
            leg.df, leg.date_strs, leg.sim, leg.summary = frame_df, date_strs, sim, summary
            leg.baseline = _build_baseline_curve(frame_df, leg.capital, date_strs)

        if first_cook is None:
            first_cook = GraphCook(program=frame_program, result=result, df=frame_df,
                                   frames=((lead.symbol, lead.interval, frame_df),)
                                   + _ref_frames_list(refs))

    # 4. The response: per group, combined, and the legacy fields.
    group_results = [
        GraphGroupResult.model_construct(
            name=leg.group.name, node_id=leg.group.node_id, path=leg.group.path,
            symbol=leg.symbol, interval=leg.interval, direction=leg.direction,
            weight=leg.group.weight, capital=round(leg.capital, 2),
            summary=leg.summary, trades=leg.sim["trades"],
            equity_curve=leg.sim["equity_curve"], baseline_curve=leg.baseline,
        )
        for leg in legs
    ]
    combined = _groups.combine([_leg_track(leg) for leg in legs], float(req.initial_capital))
    combined_result = GraphCombinedResult.model_construct(**combined)
    if len(legs) == 1:
        only = legs[0]
        legacy = (only.summary, only.sim["trades"], only.sim["equity_curve"], only.baseline)
    else:
        # Several groups: the legacy fields carry the combined result.  The
        # trades stay per group (groups[i].trades): one merged list would
        # pair one group's entry with another group's exit.
        legacy = (dict(combined["summary"]), [], combined["equity_curve"],
                  combined["baseline_curve"])

    response = GraphBacktestGroupedResponse.model_construct(
        summary=legacy[0], trades=legacy[1], equity_curve=legacy[2], baseline_curve=legacy[3],
        groups=group_results, combined=combined_result,
    )
    assert first_cook is not None
    return response, first_cook


def _leg_track(leg: _Leg):
    """A leg's bars for nodes_groups.combine."""
    df = leg.df
    keys = list(leg.date_strs)
    close = df["Close"].to_numpy(dtype=float, copy=False)
    curve = leg.sim["equity_curve"]
    own_equity = None
    if _keyed_by(curve, keys):
        # The simulator's usual curve: one point per bar, in bar order.
        # Read straight through (the same values the key lookup gives).
        equity = _curve_values(curve)
        own_equity = curve
    else:
        by_key = {p["time"]: p["value"] for p in curve}
        equity = np.array([by_key.get(k, np.nan) for k in keys], dtype=float)
    filled = pd.Series(equity).ffill().fillna(leg.capital).to_numpy()
    if own_equity is not None and filled.tobytes() != equity.tobytes():
        own_equity = None           # a gap was filled: no longer the curve's values
    equity = filled
    baseline = _curve_values(leg.baseline)
    in_pos, notional = _groups.position_track(leg.sim["trades"], keys, close)
    return _groups.LegTrack(
        times=_groups.utc_naive(df.index), keys=keys, equity=equity, baseline=baseline,
        in_pos=in_pos, notional=notional, capital=leg.capital, interval=leg.interval,
        ends=_bar_ends(df.index, leg.interval),
        final_value=float(leg.summary["final_value"]),
        num_trades=int(leg.summary.get("num_trades") or 0),
        trades=list(leg.sim["trades"]),
        # LegTrack contract: a curve whose times are the keys themselves and
        # whose values are the array (combine may return a copy of it).
        equity_curve=own_equity,
        baseline_curve=leg.baseline if _keyed_by(leg.baseline, keys) else None,
    )


def _bar_ends(index, interval: str):
    """When each bar of a leg has closed (int64 ns UTC), for combine."""
    from nodebuilder.trading.align import bar_ends

    try:
        return bar_ends(index, interval)
    except ValueError:
        return None


def _keyed_by(curve: list, keys: list) -> bool:
    """True when *curve* has one point per key whose time is that key (the
    same object, so the same type too)."""
    return len(curve) == len(keys) and all(map(operator.is_, map(_TIME, curve), keys))


_TIME = operator.itemgetter("time")
_VALUE = operator.itemgetter("value")


def _curve_values(curve: list) -> np.ndarray:
    return np.fromiter(map(_VALUE, curve), dtype=float, count=len(curve))
