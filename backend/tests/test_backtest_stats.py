from routes.backtest import _side_stats, _edge_stats


def test_side_stats_empty():
    result = _side_stats([])
    assert result == {"min": None, "max": None, "mean": None, "median": None}


def test_side_stats_single_value():
    result = _side_stats([42.0])
    assert result == {"min": 42.0, "max": 42.0, "mean": 42.0, "median": 42.0}


def test_side_stats_multiple():
    result = _side_stats([10.0, 20.0, 30.0, 40.0, 1000.0])
    assert result["min"] == 10.0
    assert result["max"] == 1000.0
    assert result["mean"] == 220.0
    assert result["median"] == 30.0


def test_side_stats_negatives():
    result = _side_stats([-50.0, -30.0, -10.0])
    assert result["min"] == -50.0
    assert result["max"] == -10.0
    assert result["mean"] == -30.0
    assert result["median"] == -30.0


def test_edge_stats_mixed():
    # 10 wins averaging $100, 5 losses averaging -$50
    gains = [100.0] * 10
    losses = [-50.0] * 5
    num_sells = 15
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 1000.0
    assert result["gross_loss"] == 250.0
    assert result["ev_per_trade"] == 50.0
    assert result["profit_factor"] == 4.0


def test_edge_stats_all_wins():
    # 5 wins, no losses — profit_factor should be None (frontend renders ∞)
    gains = [100.0, 150.0, 200.0, 50.0, 100.0]
    losses: list[float] = []
    num_sells = 5
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 600.0
    assert result["gross_loss"] == 0.0
    assert result["profit_factor"] is None
    assert result["ev_per_trade"] == 120.0


def test_edge_stats_all_losses():
    # 5 losses, no wins — profit_factor should be 0.0 (gross_profit / gross_loss == 0 / N)
    gains: list[float] = []
    losses = [-100.0, -50.0, -75.0, -25.0, -50.0]
    num_sells = 5
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 0.0
    assert result["gross_loss"] == 300.0
    assert result["profit_factor"] == 0.0
    assert result["ev_per_trade"] == -60.0


def test_edge_stats_with_breakeven():
    # 2 wins of $100, 2 losses of -$100, 1 break-even trade (excluded from gains/losses).
    # Break-even dilutes EV via num_sells.
    gains = [100.0, 100.0]
    losses = [-100.0, -100.0]
    num_sells = 5
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 200.0
    assert result["gross_loss"] == 200.0
    assert result["ev_per_trade"] == 0.0
    assert result["profit_factor"] == 1.0


def test_edge_stats_no_trades():
    # No trades at all — both EV and PF should be None
    result = _edge_stats([], [], 0)
    assert result["gross_profit"] == 0.0
    assert result["gross_loss"] == 0.0
    assert result["ev_per_trade"] is None
    assert result["profit_factor"] is None
