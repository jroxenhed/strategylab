from routes.backtest_macro import aggregate_macro


def test_aggregate_weekly_basic():
    """Weekly buckets from 2 weeks of daily equity data."""
    equity = [
        {"time": "2024-01-02", "value": 10000.0},
        {"time": "2024-01-03", "value": 10100.0},
        {"time": "2024-01-04", "value": 10050.0},
        {"time": "2024-01-05", "value": 10200.0},
        {"time": "2024-01-08", "value": 10300.0},
        {"time": "2024-01-09", "value": 10250.0},
        {"time": "2024-01-10", "value": 10400.0},
        {"time": "2024-01-11", "value": 10350.0},
        {"time": "2024-01-12", "value": 10500.0},
    ]
    trades = [
        {"type": "sell", "date": "2024-01-04", "pnl": 50.0},
        {"type": "buy", "date": "2024-01-03", "price": 100.0},
        {"type": "sell", "date": "2024-01-10", "pnl": -25.0},
    ]
    result = aggregate_macro(equity, trades, "W", 10000.0)

    assert result["bucket"] == "W"
    curve = result["macro_curve"]
    assert len(curve) == 2  # 2 calendar weeks

    w1 = curve[0]
    assert w1["open"] == 10000.0
    assert w1["high"] == 10200.0
    assert w1["low"] == 10000.0
    assert w1["close"] == 10200.0
    assert len(w1["trades"]) == 1
    assert w1["trades"][0]["pnl"] == 50.0

    w2 = curve[1]
    assert w2["open"] == 10300.0
    assert w2["high"] == 10500.0
    assert w2["low"] == 10250.0
    assert w2["close"] == 10500.0
    assert len(w2["trades"]) == 1
    assert w2["trades"][0]["pnl"] == -25.0


def test_aggregate_weekly_drawdown():
    """Drawdown tracks running peak across buckets."""
    equity = [
        {"time": "2024-01-02", "value": 10000.0},
        {"time": "2024-01-03", "value": 10500.0},
        {"time": "2024-01-08", "value": 10200.0},
        {"time": "2024-01-09", "value": 10100.0},
    ]
    result = aggregate_macro(equity, [], "W", 10000.0)
    curve = result["macro_curve"]

    assert curve[0]["drawdown_pct"] == round((10000 - 10500) / 10500 * 100, 2)
    assert curve[1]["drawdown_pct"] == round((10100 - 10500) / 10500 * 100, 2)


def test_aggregate_period_stats():
    """Period stats computed from bucket returns."""
    equity = [
        {"time": "2024-01-02", "value": 10000.0},
        {"time": "2024-01-05", "value": 10200.0},
        {"time": "2024-01-08", "value": 10200.0},
        {"time": "2024-01-12", "value": 10000.0},
        {"time": "2024-01-15", "value": 10000.0},
        {"time": "2024-01-19", "value": 10300.0},
    ]
    trades = [
        {"type": "sell", "date": "2024-01-04", "pnl": 50.0},
        {"type": "sell", "date": "2024-01-10", "pnl": -20.0},
    ]
    result = aggregate_macro(equity, trades, "W", 10000.0)
    ps = result["period_stats"]

    assert ps["label"] == "Weekly"
    assert ps["winning_pct"] == round(2 / 3 * 100, 1)
    assert ps["best_return_pct"] == round((10300 - 10000) / 10000 * 100, 2)
    assert ps["worst_return_pct"] == round((10000 - 10200) / 10200 * 100, 2)


def test_aggregate_monthly():
    """Monthly buckets group correctly."""
    equity = [
        {"time": "2024-01-15", "value": 10000.0},
        {"time": "2024-01-31", "value": 10500.0},
        {"time": "2024-02-15", "value": 10300.0},
        {"time": "2024-02-28", "value": 10800.0},
    ]
    result = aggregate_macro(equity, [], "M", 10000.0)
    assert len(result["macro_curve"]) == 2
    assert result["period_stats"]["label"] == "Monthly"


def test_aggregate_intraday_timestamps():
    """Unix timestamps (intraday data) are handled correctly."""
    equity = [
        {"time": 1704200400, "value": 10000.0},
        {"time": 1704204000, "value": 10050.0},
        {"time": 1704207600, "value": 10020.0},
        {"time": 1704286800, "value": 10100.0},
        {"time": 1704290400, "value": 10150.0},
    ]
    trades = [
        {"type": "sell", "date": 1704207600, "pnl": 20.0},
    ]
    result = aggregate_macro(equity, trades, "D", 10000.0)
    assert len(result["macro_curve"]) == 2
    assert result["macro_curve"][0]["trades"][0]["pnl"] == 20.0


def test_aggregate_empty_equity():
    """Empty equity curve returns empty result."""
    result = aggregate_macro([], [], "W", 10000.0)
    assert result["macro_curve"] == []
    assert result["period_stats"]["winning_pct"] == 0
