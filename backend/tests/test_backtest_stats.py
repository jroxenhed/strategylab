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
