# Shorting Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `direction: "long" | "short"` to backtester and live bot runner, plus refresh bot card layout with always-visible mini chart and direction styling.

**Architecture:** Single `direction` field threads through `StrategyRequest`, `BotConfig`, and all TS types. The rule engine is unchanged — direction only inverts execution math (slippage, stops, PnL, order side). Bot cards get a two-column layout with fluid-width mini sparkline showing price + equity overlay.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript/lightweight-charts (frontend), Alpaca SDK (live trading)

---

### Task 1: Backend Data Model — Add `direction` field

**Files:**
- Modify: `backend/routes/backtest.py:35-53` (StrategyRequest)
- Modify: `backend/bot_manager.py:51-74` (BotConfig)
- Modify: `backend/routes/trading.py:30-52` (_log_trade)
- Test: `backend/tests/test_models.py`

- [ ] **Step 1: Write tests for direction field defaults and validation**

In `backend/tests/test_models.py`, add:

```python
from bot_manager import BotConfig
from signal_engine import Rule


def test_strategy_request_direction_defaults_long():
    req = make_req()
    assert req.direction == "long"


def test_strategy_request_direction_accepts_short():
    req = make_req(direction="short")
    assert req.direction == "short"


def test_bot_config_direction_defaults_long():
    cfg = BotConfig(
        strategy_name="test", symbol="AAPL", interval="5m",
        buy_rules=[], sell_rules=[], allocated_capital=1000,
    )
    assert cfg.direction == "long"


def test_bot_config_direction_accepts_short():
    cfg = BotConfig(
        strategy_name="test", symbol="AAPL", interval="5m",
        buy_rules=[], sell_rules=[], allocated_capital=1000,
        direction="short",
    )
    assert cfg.direction == "short"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: FAIL — `direction` field not yet defined

- [ ] **Step 3: Add `direction` field to StrategyRequest**

In `backend/routes/backtest.py`, add after line 52 (`source: str = "yahoo"`):

```python
    direction: str = "long"  # "long" | "short"
```

- [ ] **Step 4: Add `direction` field to BotConfig**

In `backend/bot_manager.py`, add after line 73 (`data_source: str = "alpaca-iex"`):

```python
    direction: str = "long"  # "long" | "short"
```

- [ ] **Step 5: Add `direction` parameter to `_log_trade`**

In `backend/routes/trading.py`, update `_log_trade` signature and the appended dict:

```python
def _log_trade(symbol: str, side: str, qty: float, price: float | None,
               source: str, stop_loss_price: float | None = None,
               reason: str | None = None, expected_price: float | None = None,
               direction: str = "long"):
    """Append a trade entry to the journal."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if JOURNAL_PATH.exists():
        journal = json.loads(JOURNAL_PATH.read_text())
    else:
        journal = {"trades": []}
    journal["trades"].append({
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "stop_loss_price": stop_loss_price,
        "source": source,
        "reason": reason,
        "expected_price": expected_price,
        "direction": direction,
    })
    JOURNAL_PATH.write_text(json.dumps(journal, indent=2))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_models.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add backend/routes/backtest.py backend/bot_manager.py backend/routes/trading.py backend/tests/test_models.py
git commit -m "feat: add direction field to StrategyRequest, BotConfig, and trade journal"
```

---

### Task 2: Backtest Engine — Short direction math

**Files:**
- Modify: `backend/routes/backtest.py:62-318` (run_backtest)
- Test: `backend/tests/test_backtest_short.py` (new)

- [ ] **Step 1: Create test file with short backtest tests**

Create `backend/tests/test_backtest_short.py`:

```python
"""Tests for short-direction backtesting logic."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import numpy as np
from unittest.mock import patch
from routes.backtest import StrategyRequest, run_backtest
from signal_engine import Rule


def _make_df(prices: list[float]) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame from close prices."""
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": [1000000] * len(prices),
    }, index=dates)


def _req_short(**kwargs) -> StrategyRequest:
    defaults = dict(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="rsi", condition="crosses_below", value=70)],
        sell_rules=[Rule(indicator="rsi", condition="crosses_above", value=50)],
        initial_capital=10000.0, position_size=1.0,
    )
    return StrategyRequest(**{**defaults, **kwargs})


@patch("routes.backtest._fetch")
def test_short_trade_pnl_positive_when_price_drops(mock_fetch):
    """Short entry at 100, price drops to 90 → positive PnL."""
    prices = [100] * 20 + [95, 90]  # enough bars for RSI warmup
    mock_fetch.return_value = _make_df(prices)

    # Use always-true entry/exit for simplicity
    req = StrategyRequest(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="close", condition="less_than", value=999)],
        sell_rules=[Rule(indicator="close", condition="less_than", value=91)],
        initial_capital=10000.0, position_size=1.0,
    )
    result = run_backtest(req)
    sells = [t for t in result["trades"] if t["type"] == "cover"]
    assert len(sells) >= 1
    assert sells[0]["pnl"] > 0  # price dropped, short profits


@patch("routes.backtest._fetch")
def test_short_trade_pnl_negative_when_price_rises(mock_fetch):
    """Short entry at 100, price rises to 110 → negative PnL."""
    prices = [100] * 20 + [105, 110]
    mock_fetch.return_value = _make_df(prices)

    req = StrategyRequest(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="close", condition="less_than", value=999)],
        sell_rules=[Rule(indicator="close", condition="greater_than", value=109)],
        initial_capital=10000.0, position_size=1.0,
    )
    result = run_backtest(req)
    covers = [t for t in result["trades"] if t["type"] == "cover"]
    assert len(covers) >= 1
    assert covers[0]["pnl"] < 0  # price rose, short loses


@patch("routes.backtest._fetch")
def test_short_trade_types_are_short_and_cover(mock_fetch):
    """Short trades should use 'short'/'cover' instead of 'buy'/'sell'."""
    prices = [100] * 20 + [95, 90]
    mock_fetch.return_value = _make_df(prices)

    req = StrategyRequest(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="close", condition="less_than", value=999)],
        sell_rules=[Rule(indicator="close", condition="less_than", value=91)],
        initial_capital=10000.0, position_size=1.0,
    )
    result = run_backtest(req)
    types = [t["type"] for t in result["trades"]]
    assert "short" in types
    assert "cover" in types
    assert "buy" not in types
    assert "sell" not in types


@patch("routes.backtest._fetch")
def test_short_stop_loss_triggers_above_entry(mock_fetch):
    """Short stop loss should trigger when price rises above entry."""
    prices = [100] * 20 + [105, 110]  # price rises
    mock_fetch.return_value = _make_df(prices)

    req = StrategyRequest(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="close", condition="less_than", value=999)],
        sell_rules=[],
        initial_capital=10000.0, position_size=1.0,
        stop_loss_pct=3.0,  # 3% above entry
    )
    result = run_backtest(req)
    covers = [t for t in result["trades"] if t["type"] == "cover"]
    assert len(covers) >= 1
    assert covers[0].get("stop_loss") is True


@patch("routes.backtest._fetch")
def test_short_slippage_direction(mock_fetch):
    """Short entry slippage should lower fill (worse for seller), exit slippage should raise fill (worse for buyer)."""
    prices = [100] * 20 + [95, 90]
    mock_fetch.return_value = _make_df(prices)

    req = StrategyRequest(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="close", condition="less_than", value=999)],
        sell_rules=[Rule(indicator="close", condition="less_than", value=91)],
        initial_capital=10000.0, position_size=1.0,
        slippage_pct=1.0,
    )
    result = run_backtest(req)
    entry = [t for t in result["trades"] if t["type"] == "short"][0]
    # Entry fill should be LOWER than market price (slippage against short seller)
    # The market price for entry is close[i], fill = close[i] * (1 - slippage)
    assert entry["slippage"] > 0  # slippage cost is positive


@patch("routes.backtest._fetch")
def test_long_still_works(mock_fetch):
    """Existing long backtests should be unaffected."""
    prices = [100] * 20 + [105, 110]
    mock_fetch.return_value = _make_df(prices)

    req = StrategyRequest(
        ticker="TEST", direction="long",
        buy_rules=[Rule(indicator="close", condition="less_than", value=999)],
        sell_rules=[Rule(indicator="close", condition="greater_than", value=109)],
        initial_capital=10000.0, position_size=1.0,
    )
    result = run_backtest(req)
    types = [t["type"] for t in result["trades"]]
    assert "buy" in types
    assert "sell" in types
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_backtest_short.py -v`
Expected: FAIL — short logic not implemented yet

- [ ] **Step 3: Implement short direction in `run_backtest()`**

In `backend/routes/backtest.py`, modify `run_backtest()`. Add a `is_short` flag at the top of the function:

```python
    is_short = req.direction == "short"
```

Then modify each section. Here are the key changes within the `for i in range(len(df)):` loop:

**Entry block** (currently starts at `if position == 0 and hour_ok and eval_rules(...)`):

Replace the entry block with:

```python
        if position == 0 and hour_ok and eval_rules(req.buy_rules, req.buy_logic, indicators, i):
            # Dynamic sizing: reduce position after consecutive stop losses
            effective_size = req.position_size
            if ds and ds.enabled and consec_sl_count >= ds.consec_sls:
                effective_size = req.position_size * (ds.reduced_pct / 100)

            # Slippage: short entry fills lower (worse for seller), long entry fills higher (worse for buyer)
            if is_short:
                fill_price = price * (1 - req.slippage_pct / 100)
            else:
                fill_price = price * (1 + req.slippage_pct / 100)
            shares = (capital * effective_size) / fill_price
            commission = shares * fill_price * req.commission_pct / 100
            position = shares
            entry_price = fill_price
            capital -= shares * fill_price + commission  # margin/collateral for short
            trail_peak = fill_price
            trail_stop_price = None
            entry_slippage = abs(shares * (fill_price - price))
            trade_type = "short" if is_short else "buy"
            trades.append({
                "type": trade_type, "date": date, "price": round(fill_price, 4),
                "shares": round(shares, 4),
                "slippage": round(entry_slippage, 2),
                "commission": round(commission, 2),
                "direction": req.direction,
            })
            if signal_trace is not None:
                signal_trace.append({
                    "date": date, "price": round(price, 4), "position": "entered",
                    "action": "SHORT" if is_short else "BUY",
                    "buy_rules": _trace_rules(req.buy_rules, indicators, i, "buy"),
                })
```

**Exit block** — replace the `elif position > 0:` section. The trailing stop and stop loss checks change based on direction:

```python
        elif position > 0:
            # Update trailing stop peak/trough and compute trail_stop_price
            trail_hit = False
            if ts:
                if is_short:
                    # Track trough (lowest price) for shorts
                    source_price = low.iloc[i] if ts.source == "high" else price
                    threshold = entry_price * (1 - ts.activate_pct / 100)
                    if not ts.activate_on_profit or source_price <= threshold:
                        trail_peak = min(trail_peak, source_price)
                    if ts.type == "pct":
                        trail_stop_price = trail_peak * (1 + ts.value / 100)
                    else:  # atr
                        atr_val = atr.iloc[i] if atr is not None and not pd.isna(atr.iloc[i]) else 0.0
                        trail_stop_price = trail_peak + ts.value * atr_val
                    trail_hit = high.iloc[i] >= trail_stop_price
                else:
                    source_price = high.iloc[i] if ts.source == "high" else price
                    threshold = entry_price * (1 + ts.activate_pct / 100)
                    if not ts.activate_on_profit or source_price >= threshold:
                        trail_peak = max(trail_peak, source_price)
                    if ts.type == "pct":
                        trail_stop_price = trail_peak * (1 - ts.value / 100)
                    else:  # atr
                        atr_val = atr.iloc[i] if atr is not None and not pd.isna(atr.iloc[i]) else 0.0
                        trail_stop_price = trail_peak - ts.value * atr_val
                    trail_hit = low.iloc[i] <= trail_stop_price

            # Check fixed stop loss
            if is_short:
                stop_price_limit = entry_price * (1 + req.stop_loss_pct / 100) if (req.stop_loss_pct and req.stop_loss_pct > 0) else None
                stop_hit = stop_price_limit is not None and high.iloc[i] >= stop_price_limit
            else:
                stop_price_limit = entry_price * (1 - req.stop_loss_pct / 100) if (req.stop_loss_pct and req.stop_loss_pct > 0) else None
                stop_hit = stop_price_limit is not None and low.iloc[i] <= stop_price_limit

            # Exit priority: fixed stop beats trailing stop (it's the harder floor)
            if stop_hit:
                raw_exit = stop_price_limit
                exit_reason = "stop_loss"
            elif trail_hit:
                raw_exit = trail_stop_price
                exit_reason = "trailing_stop"
            else:
                raw_exit = price
                exit_reason = "signal"

            # Slippage: short cover fills higher (worse), long sell fills lower (worse)
            if is_short:
                exit_price = raw_exit * (1 + req.slippage_pct / 100)
            else:
                exit_price = raw_exit * (1 - req.slippage_pct / 100)

            sell_fired = eval_rules(req.sell_rules, req.sell_logic, indicators, i)
            if stop_hit or trail_hit or sell_fired:
                proceeds = position * exit_price
                commission = proceeds * req.commission_pct / 100
                exit_slippage = abs(position * (raw_exit - exit_price))
                if is_short:
                    pnl = position * (entry_price - exit_price) - commission
                else:
                    pnl = (proceeds - commission) - position * entry_price
                trade_type = "cover" if is_short else "sell"
                trades.append({
                    "type": trade_type,
                    "date": date,
                    "price": round(exit_price, 4),
                    "shares": round(position, 4),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
                    "stop_loss": exit_reason == "stop_loss",
                    "trailing_stop": exit_reason == "trailing_stop",
                    "slippage": round(exit_slippage, 2),
                    "commission": round(commission, 2),
                    "direction": req.direction,
                })
                if is_short:
                    capital += position * entry_price + pnl  # return collateral + profit/loss
                else:
                    capital += proceeds - commission
                position = 0.0
                trail_peak = 0.0
                trail_stop_price = None
                if exit_reason == "stop_loss":
                    consec_sl_count += 1
                else:
                    consec_sl_count = 0
                if signal_trace is not None:
                    action = "STOP_LOSS" if exit_reason == "stop_loss" else "TRAIL_STOP" if exit_reason == "trailing_stop" else ("COVER" if is_short else "SELL")
                    signal_trace.append({
                        "date": date, "price": round(price, 4), "position": "exited",
                        "action": action,
                        "sell_rules": _trace_rules(req.sell_rules, indicators, i, "sell"),
                    })
            elif signal_trace is not None:
                sell_details = _trace_rules(req.sell_rules, indicators, i, "sell")
                if any(d["result"] for d in sell_details if not d.get("muted")):
                    signal_trace.append({
                        "date": date, "price": round(price, 4), "position": "holding",
                        "action": "SELL_PARTIAL (AND not met)",
                        "sell_rules": sell_details,
                    })
```

**Equity calculation** — replace the line after the if/elif blocks:

```python
        if is_short and position > 0:
            # Short equity: collateral already deducted from capital, add back value at current price
            unrealized = position * (entry_price - price)
            total_value = capital + position * entry_price + unrealized
        else:
            total_value = capital + (position * price if position > 0 else 0)
        equity.append({"time": date, "value": round(total_value, 2)})
```

**Summary stats** — update `sell_trades` filter to include covers:

```python
        exit_type = "cover" if is_short else "sell"
        sell_trades = [t for t in trades if t["type"] == exit_type]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_backtest_short.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run existing tests to confirm no regressions**

Run: `cd backend && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backend/routes/backtest.py backend/tests/test_backtest_short.py
git commit -m "feat: implement short direction in backtest engine"
```

---

### Task 3: Bot Runner — Short direction execution

**Files:**
- Modify: `backend/bot_manager.py:144-550` (BotRunner)
- Modify: `backend/bot_manager.py:771-788` (list_bots)

- [ ] **Step 1: Update `list_bots` to include `direction`**

In `backend/bot_manager.py`, in the `list_bots` method, add `direction` to the result dict after `"data_source"`:

```python
                "direction": config.direction,
```

- [ ] **Step 2: Update entry logic in `_tick()` for short direction**

In `bot_manager.py`, in the `_tick()` method, find the entry order section (around line 358-398). Add `is_short = cfg.direction == "short"` near the top of `_tick()` (after `cfg = self.config`).

Update all `OrderSide.BUY` to be conditional:

```python
                    entry_side = OrderSide.SELL if is_short else OrderSide.BUY
```

For short entries, do NOT use OTO brackets (bot manages stops via polling):

```python
                    if is_short:
                        # Short: plain market sell, bot manages stops via polling
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY,
                        )
                    elif cfg.trailing_stop and cfg.stop_loss_pct:
                        stop_price = round(price * (1 - cfg.stop_loss_pct / 100), 2)
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                            order_class=OrderClass.OTO,
                            stop_loss=StopLossRequest(stop_price=stop_price),
                        )
                    elif cfg.trailing_stop:
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                        )
                    elif cfg.stop_loss_pct:
                        stop_price = round(price * (1 - cfg.stop_loss_pct / 100), 2)
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                            order_class=OrderClass.OTO,
                            stop_loss=StopLossRequest(stop_price=stop_price),
                        )
                    else:
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                        )
```

Update slippage tracking for entry:

```python
                if is_short:
                    slippage = price - fill_price  # lower fill is worse for short
                else:
                    slippage = fill_price - price
                slippage_pct = (slippage / price) * 100 if price else 0
```

Update trade log and journal calls to include direction:

```python
                side_label = "SHORT" if is_short else "BUY"
                self._log("TRADE", f"{side_label} {qty} {cfg.symbol} @ {fill_price:.2f} ...")
                _log_trade(cfg.symbol, "short" if is_short else "buy", qty, fill_price,
                           source="bot", reason="entry", expected_price=price,
                           direction=cfg.direction)
```

- [ ] **Step 3: Update position detection for shorts**

In `_tick()`, where `alpaca_qty` is read (around line 230-250), update to handle negative qty from Alpaca shorts:

```python
            # Alpaca returns negative qty for short positions
            alpaca_qty = abs(float(pos.qty)) if pos else 0
```

Also, the existing position-vanished detection checks for `had_position and not has_alpaca_pos`. This should work for shorts too since `close_position()` clears the position.

- [ ] **Step 4: Update exit logic for short direction**

In the exit section (around line 430-527), update trailing stop tracking:

```python
            if cfg.trailing_stop and state.entry_price is not None:
                ts = cfg.trailing_stop
                if is_short:
                    source_price = float(df["Low"].iloc[-1]) if ts.source == "high" else price
                    activated = (not ts.activate_on_profit) or (
                        source_price <= state.entry_price * (1 - ts.activate_pct / 100)
                    )
                    if activated:
                        if state.trail_peak is None or source_price < state.trail_peak:
                            state.trail_peak = source_price
                        atr_val = float(indicators.get("atr", {}).get(i, 0) or 0)
                        if ts.type == "pct":
                            state.trail_stop_price = state.trail_peak * (1 + ts.value / 100)
                        elif ts.type == "atr" and atr_val:
                            state.trail_stop_price = state.trail_peak + ts.value * atr_val
                else:
                    source_price = float(df["High"].iloc[-1]) if ts.source == "high" else price
                    activated = (not ts.activate_on_profit) or (
                        source_price >= state.entry_price * (1 + ts.activate_pct / 100)
                    )
                    if activated:
                        if state.trail_peak is None or source_price > state.trail_peak:
                            state.trail_peak = source_price
                        atr_val = float(indicators.get("atr", {}).get(i, 0) or 0)
                        if ts.type == "pct":
                            state.trail_stop_price = state.trail_peak * (1 - ts.value / 100)
                        elif ts.type == "atr" and atr_val:
                            state.trail_stop_price = state.trail_peak - ts.value * atr_val
```

Update stop-loss check:

```python
            if is_short:
                if cfg.stop_loss_pct and state.entry_price:
                    if price >= state.entry_price * (1 + cfg.stop_loss_pct / 100):
                        exit_reason = "stop_loss"
                if exit_reason is None and cfg.trailing_stop and state.trail_stop_price:
                    if price >= state.trail_stop_price:
                        exit_reason = "trailing_stop"
            else:
                if cfg.stop_loss_pct and state.entry_price and not cfg.trailing_stop:
                    if price <= state.entry_price * (1 - cfg.stop_loss_pct / 100):
                        exit_reason = "stop_loss"
                if exit_reason is None and cfg.trailing_stop and state.trail_stop_price:
                    if price <= state.trail_stop_price:
                        exit_reason = "trailing_stop"
```

Update PnL calculation on exit:

```python
                if is_short:
                    pnl = (state.entry_price - sell_fill) * alpaca_qty if state.entry_price else 0
                else:
                    pnl = (sell_fill - state.entry_price) * alpaca_qty if state.entry_price else 0
```

Update slippage tracking on exit and journal log:

```python
                if is_short:
                    slippage = sell_fill - price  # higher cover fill is worse
                else:
                    slippage = sell_fill - price
                slippage_pct = (slippage / price) * 100 if price else 0
```

```python
                _log_trade(cfg.symbol, "cover" if is_short else "sell", alpaca_qty, sell_fill,
                           source="bot", reason=exit_reason, expected_price=price,
                           direction=cfg.direction)
```

- [ ] **Step 5: Update PnL in position-vanished detection**

In the section where position disappears (around line 280-330), update PnL for shorts:

```python
                if is_short:
                    pnl = (state.entry_price - exit_price) * sell_qty if sell_qty else 0
                else:
                    pnl = (exit_price - state.entry_price) * sell_qty if sell_qty else 0
```

- [ ] **Step 6: Update manual buy endpoint**

In `backend/routes/bots.py`, find the manual buy endpoint. Update it to check `config.direction` and send `OrderSide.SELL` for short bots. Also update in `backend/bot_manager.py` the `manual_buy` method (around line 720-740):

```python
    async def manual_buy(self, bot_id: str):
        ...
        cfg = config
        is_short = cfg.direction == "short"
        order_req = MarketOrderRequest(
            symbol=cfg.symbol.upper(),
            qty=qty,
            side=OrderSide.SELL if is_short else OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
```

- [ ] **Step 7: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add backend/bot_manager.py backend/routes/bots.py backend/routes/trading.py
git commit -m "feat: implement short direction in bot runner"
```

---

### Task 4: Frontend Types — Add `direction` to all interfaces

**Files:**
- Modify: `frontend/src/shared/types/index.ts`

- [ ] **Step 1: Add `direction` to TS types**

In `frontend/src/shared/types/index.ts`:

Add to `StrategyRequest` (after `debug?: boolean`):
```typescript
  direction?: 'long' | 'short'
```

Add to `SavedStrategy` (after `commission: number | ''`):
```typescript
  direction: 'long' | 'short'
```

Update `Trade` type:
```typescript
export interface Trade {
  type: 'buy' | 'sell' | 'short' | 'cover'
  date: string | number
  price: number
  shares: number
  pnl?: number
  pnl_pct?: number
  stop_loss?: boolean
  trailing_stop?: boolean
  slippage?: number
  commission?: number
  direction?: 'long' | 'short'
}
```

Add to `BotConfig` (after `data_source?: string`):
```typescript
  direction?: 'long' | 'short'
```

Add to `BotSummary` (after `has_position?: boolean`):
```typescript
  direction?: 'long' | 'short'
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/shared/types/index.ts
git commit -m "feat: add direction to frontend TypeScript types"
```

---

### Task 5: Frontend — Strategy Builder direction toggle

**Files:**
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx`

- [ ] **Step 1: Add direction state**

After the existing state declarations (around line 60), add:

```typescript
  const [direction, setDirection] = useState<'long' | 'short'>(saved?.direction ?? 'long')
```

- [ ] **Step 2: Include direction in localStorage persistence**

In the `useEffect` that writes to `STRATEGY_STORAGE_KEY` (around line 118-123), add `direction` to the persisted object.

- [ ] **Step 3: Include direction in backtest request**

In `runBacktest()` (around line 132-143), add `direction` to the `req` object:

```typescript
        direction,
```

- [ ] **Step 4: Include direction in saved strategy**

In the save-strategy logic, add `direction` to the `SavedStrategy` object.

- [ ] **Step 5: Add direction toggle UI**

Add a Long/Short toggle at the top of the strategy builder, above the rule sections. A simple segmented control:

```tsx
<div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
  {(['long', 'short'] as const).map(d => (
    <button
      key={d}
      onClick={() => setDirection(d)}
      style={{
        padding: '4px 12px', fontSize: 12, borderRadius: 4, border: 'none',
        cursor: 'pointer', textTransform: 'uppercase', fontWeight: 600,
        background: direction === d
          ? (d === 'long' ? '#1a3a2a' : '#3a1a1a')
          : '#161b22',
        color: direction === d
          ? (d === 'long' ? '#26a69a' : '#ef5350')
          : '#666',
      }}
    >
      {d}
    </button>
  ))}
</div>
```

- [ ] **Step 6: Swap rule labels based on direction**

When `direction === 'short'`, change the section headers from "Buy Rules" to "Entry Rules" and "Sell Rules" to "Exit Rules". Find the labels in the JSX and make them conditional:

```tsx
{direction === 'short' ? 'Entry Rules' : 'Buy Rules'}
{direction === 'short' ? 'Exit Rules' : 'Sell Rules'}
```

- [ ] **Step 7: Verify backtest works with direction toggle**

Start the app with `./start.sh`, select a strategy, toggle to Short, run a backtest. Verify the trades table shows "short"/"cover" types.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/strategy/StrategyBuilder.tsx
git commit -m "feat: add direction toggle to strategy builder"
```

---

### Task 6: Frontend — Results display for short trades

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx`

- [ ] **Step 1: Update trade filtering**

In `Results.tsx`, the code filters trades by `t.type === 'sell'` for completed trades (line 23) and `t.type === 'buy'` for entry trades (line 162). Update to include short types:

```typescript
const exits = trades.filter(t => t.type === 'sell' || t.type === 'cover')
```

And for entries:

```typescript
const entry = trades.filter(t => t.type === 'buy' || t.type === 'short')[i]
```

- [ ] **Step 2: Update trade type display**

Wherever trade type is displayed, capitalize it. The types `"short"` and `"cover"` should display as-is (or title case). Check for any color coding that assumes only buy/sell.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat: update Results display for short trade types"
```

---

### Task 7: Frontend — Bot Card layout refresh + direction styling

**Files:**
- Modify: `frontend/src/features/trading/BotControlCenter.tsx`

This is the biggest frontend task. It involves:
1. Two-column card layout (stats left, chart right)
2. Direction badge + background tint
3. Always-visible mini chart with price + equity overlay
4. Manual Buy → Manual Entry for shorts

- [ ] **Step 1: Update BotCard to two-column layout**

Replace the outer `<div>` in BotCard with a two-column flex layout. The left column contains the existing header, stats, backtest summary, and buttons. The right column contains the mini sparkline:

```tsx
  const bgTint = (summary.direction === 'short')
    ? 'rgba(200, 0, 0, 0.03)'
    : 'rgba(0, 200, 0, 0.03)'

  return (
    <div style={{
      background: `linear-gradient(135deg, ${bgTint}, #161b22)`,
      border: '1px solid #1e2530', borderRadius: 6,
      padding: 12, display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      {/* Main row: stats left, chart right */}
      <div style={{ display: 'flex', gap: 12 }}>
        {/* Left column: info + buttons */}
        <div style={{ flex: '0 0 auto', display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
          {/* ... existing header, stats, backtest summary, buttons ... */}
        </div>

        {/* Right column: mini chart, always visible */}
        <div style={{ flex: 1, minWidth: 120, minHeight: 60 }}>
          <MiniSparkline
            equityData={detail?.state.equity_snapshots ?? []}
            botId={summary.bot_id}
          />
        </div>
      </div>

      {/* Expandable: log only */}
      {expanded && (
        <ActivityLog entries={detail?.state.activity_log ?? []} />
      )}
    </div>
  )
```

- [ ] **Step 2: Add direction badge**

In the header row, after the strategy name, add a direction pill:

```tsx
<span style={{
  fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 3,
  background: summary.direction === 'short' ? 'rgba(239,83,80,0.15)' : 'rgba(38,166,154,0.15)',
  color: summary.direction === 'short' ? '#ef5350' : '#26a69a',
  textTransform: 'uppercase', letterSpacing: 0.5,
}}>
  {summary.direction ?? 'long'}
</span>
```

- [ ] **Step 3: Update Manual Buy button for shorts**

Change the Buy button text and behavior based on direction:

```tsx
<button
  onClick={onManualBuy}
  disabled={!running || summary.has_position}
  style={btnStyle('#1a3a2a', !running || summary.has_position)}
>{summary.direction === 'short' ? 'Short' : 'Buy'}</button>
```

- [ ] **Step 4: Load detail always (not just on expand) for mini chart**

Currently, detail is only fetched when `expanded` is true. Change this so the bot detail (which includes equity_snapshots) is always polled for running bots:

```tsx
  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const d = await fetchBotDetail(summary.bot_id)
        if (active) setDetail(d)
      } catch {}
    }
    load()
    // Poll when running or expanded
    if (running || expanded) {
      const id = setInterval(load, 2000)
      return () => { active = false; clearInterval(id) }
    }
    return () => { active = false }
  }, [expanded, running, summary.bot_id])
```

- [ ] **Step 5: Enhance MiniSparkline to show price + equity overlay**

Update the `MiniSparkline` component to accept both equity data and a `botId` for fetching price data. It should overlay the equity curve on top of a price line:

```tsx
function MiniSparkline({ equityData, botId }: {
  equityData: { time: string; value: number }[]
  botId: string
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: 60,
      layout: { background: { color: 'transparent' }, textColor: '#aaa' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      leftPriceScale: { visible: false },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScroll: false,
      handleScale: false,
    })

    // Equity curve (baseline around 0)
    if (equityData.length >= 2) {
      const eqSeries = chart.addSeries(BaselineSeries, {
        baseValue: { type: 'price', price: 0 },
        topLineColor: '#26a69a',
        topFillColor1: 'rgba(38,166,154,0.2)',
        topFillColor2: 'rgba(38,166,154,0.02)',
        bottomLineColor: '#ef5350',
        bottomFillColor1: 'rgba(239,83,80,0.02)',
        bottomFillColor2: 'rgba(239,83,80,0.2)',
        lineWidth: 1,
        priceScaleId: 'equity',
      })
      const mapped = equityData.map((d, i) => ({ time: i + 1, value: d.value })) as any
      eqSeries.setData(mapped)
    }

    chart.timeScale().fitContent()

    // Resize observer for fluid width
    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [equityData, botId])

  return <div ref={ref} style={{ width: '100%', height: 60 }} />
}
```

Note: For the price overlay, we can use `last_price` from the bot's polling data to build a price series over time. This requires adding a `price_snapshots` list to `BotState` (alongside `equity_snapshots`), populated each tick. This is a small addition to the bot runner — add `{"time": ..., "value": price}` to `state.price_snapshots` in `_tick()` alongside the existing equity snapshot logic. Then pass both series to `MiniSparkline`. The price line uses a separate `priceScaleId` so it auto-scales independently from equity.

- [ ] **Step 6: Add ResizeObserver to handle fluid width**

Already included in Step 5 above via `ResizeObserver`.

- [ ] **Step 7: Verify the card layout visually**

Start the app, check bot cards display in two-column layout. Verify:
- Mini chart visible on all cards without expanding
- Direction badge shows correctly
- Background tint differs between long/short bots
- Chart grows when window is wider
- Expand only shows activity log

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/trading/BotControlCenter.tsx
git commit -m "feat: bot card two-column layout with direction badge and always-visible sparkline"
```

---

### Task 8: Frontend — Add Bot Bar direction field

**Files:**
- Modify: `frontend/src/features/trading/BotControlCenter.tsx` (AddBotBar function, around line 379)

- [ ] **Step 1: Add direction state to AddBotBar**

In the `AddBotBar` function, add:

```tsx
const [direction, setDirection] = useState<'long' | 'short'>('long')
```

- [ ] **Step 2: Add direction toggle to the form row**

Between the data source dropdown and the allocation input, add a direction toggle:

```tsx
{/* Direction */}
<select value={direction} onChange={e => setDirection(e.target.value as 'long' | 'short')} style={inputStyle}>
  <option value="long">Long</option>
  <option value="short">Short</option>
</select>
```

- [ ] **Step 3: Include direction in the bot creation payload**

In `handleAdd`, add `direction` to the object passed to `onAdd`:

```typescript
        direction,
```

- [ ] **Step 4: Verify adding a short bot works**

Start the app, select "Short" in the direction dropdown, create a bot. Verify it appears with the SHORT badge on the card.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/trading/BotControlCenter.tsx
git commit -m "feat: add direction selector to AddBotBar"
```

---

### Task 9: Integration Test — End-to-end short backtest

**Files:**
- Test: `backend/tests/test_backtest_short.py` (add integration-style test)

- [ ] **Step 1: Add an integration test that exercises the full API**

Add to `backend/tests/test_backtest_short.py`:

```python
from fastapi.testclient import TestClient
from main import app


def test_short_backtest_api_endpoint():
    """Test the /api/backtest endpoint with direction=short."""
    client = TestClient(app)
    resp = client.post("/api/backtest", json={
        "ticker": "AAPL",
        "start": "2024-01-01",
        "end": "2024-06-01",
        "interval": "1d",
        "buy_rules": [{"indicator": "rsi", "condition": "crosses_above", "value": 70}],
        "sell_rules": [{"indicator": "rsi", "condition": "crosses_below", "value": 50}],
        "direction": "short",
        "initial_capital": 10000,
        "position_size": 1.0,
        "source": "yahoo",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "trades" in data
    # All trades should be short/cover type
    for t in data["trades"]:
        assert t["type"] in ("short", "cover"), f"Unexpected trade type: {t['type']}"
```

- [ ] **Step 2: Run the integration test**

Run: `cd backend && python -m pytest tests/test_backtest_short.py::test_short_backtest_api_endpoint -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_backtest_short.py
git commit -m "test: add integration test for short backtest API"
```

---

### Task 10: Final verification and cleanup

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Start the full app and test manually**

Run: `./start.sh`

Verify:
1. Strategy Builder: Long/Short toggle visible, labels swap to Entry/Exit for shorts
2. Backtest a short strategy: trades show as short/cover, PnL makes sense (positive when price drops)
3. Bot cards: two-column layout, direction badge, background tint, fluid chart width
4. Add a short bot via AddBotBar: direction dropdown, bot appears with SHORT badge
5. Existing long bots and backtests still work identically

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "chore: final cleanup for shorting support"
```

- [ ] **Step 4: Push to remote**

```bash
git push
```
