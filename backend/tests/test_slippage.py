"""Tests for backend/slippage.py — sign convention, policy, window behavior."""
import json
import pytest
from pathlib import Path

from slippage import (
    slippage_cost_bps,
    fill_bias_bps,
    decide_modeled_bps,
    ModeledSlippage,
    SLIPPAGE_DEFAULT_BPS,
    SLIPPAGE_MIN_FILLS,
    SLIPPAGE_WINDOW,
)


# ---------- slippage_cost_bps: side-inversion table ----------

@pytest.mark.parametrize("side,expected,fill,want", [
    # side,    expected, fill,   cost_bps
    ("buy",    100.0,    100.10, 10.0),   # worse-above
    ("buy",    100.0,     99.90,  0.0),   # favorable clamps to 0
    ("cover",  100.0,    100.10, 10.0),
    ("cover",  100.0,     99.90,  0.0),
    ("sell",   100.0,     99.90, 10.0),   # worse-below
    ("sell",   100.0,    100.10,  0.0),
    ("short",  100.0,     99.90, 10.0),
    ("short",  100.0,    100.10,  0.0),
])
def test_cost_bps_side_table(side, expected, fill, want):
    assert slippage_cost_bps(side, expected, fill) == pytest.approx(want, abs=1e-6)


def test_cost_bps_case_insensitive():
    assert slippage_cost_bps("BUY", 100.0, 100.10) == pytest.approx(10.0)
    assert slippage_cost_bps("Sell", 100.0, 99.90) == pytest.approx(10.0)


def test_cost_bps_zero_expected_is_zero():
    # Defensive: division-by-zero guard returns 0 rather than raising.
    assert slippage_cost_bps("buy", 0.0, 1.0) == 0.0


# ---------- fill_bias_bps: signed, positive = favorable ----------

def test_bias_buy_favorable_is_positive():
    assert fill_bias_bps("buy", 100.0, 99.90) == pytest.approx(10.0)


def test_bias_buy_unfavorable_is_negative():
    assert fill_bias_bps("buy", 100.0, 100.10) == pytest.approx(-10.0)


def test_bias_sell_favorable_is_positive():
    assert fill_bias_bps("sell", 100.0, 100.10) == pytest.approx(10.0)


def test_bias_sell_unfavorable_is_negative():
    assert fill_bias_bps("sell", 100.0, 99.90) == pytest.approx(-10.0)


def test_bias_symmetry_with_cost():
    # Unfavorable: bias = -cost. Favorable: bias > 0, cost = 0.
    for side in ("buy", "sell", "cover", "short"):
        bad_exp, bad_fill = (100.0, 100.10) if side in ("buy", "cover") else (100.0, 99.90)
        good_exp, good_fill = (100.0, 99.90) if side in ("buy", "cover") else (100.0, 100.10)
        assert fill_bias_bps(side, bad_exp, bad_fill) == pytest.approx(
            -slippage_cost_bps(side, bad_exp, bad_fill)
        )
        assert fill_bias_bps(side, good_exp, good_fill) > 0
        assert slippage_cost_bps(side, good_exp, good_fill) == 0.0


# ---------- decide_modeled_bps: policy ----------

@pytest.fixture
def fake_journal(tmp_path, monkeypatch):
    """Point both journal module and slippage module at an empty temp journal.

    The on-disk shape is {"trades": [...]} — see backend/journal.py._log_trade.
    """
    f = tmp_path / "trade_journal.json"
    f.write_text('{"trades": []}')
    import journal
    import slippage as slip_mod
    monkeypatch.setattr(journal, "JOURNAL_PATH", f)
    monkeypatch.setattr(slip_mod, "JOURNAL_PATH", f)
    return f


def _write_fills(f: Path, fills: list[dict]):
    f.write_text(json.dumps({"trades": fills}))


def _fill(symbol: str, side: str, expected: float, price: float):
    return {"symbol": symbol, "side": side, "expected_price": expected, "price": price}


def test_policy_empty_journal(fake_journal):
    result = decide_modeled_bps("AAPL")
    assert result == ModeledSlippage(
        modeled_bps=SLIPPAGE_DEFAULT_BPS,
        measured_bps=None,
        fill_bias_bps=None,
        fill_count=0,
        source="default",
    )


def test_policy_below_min_fills_uses_default(fake_journal):
    # 5 unfavorable fills: measured is real but fill_count < MIN
    fills = [_fill("AAPL", "buy", 100.0, 100.05) for _ in range(5)]
    _write_fills(fake_journal, fills)

    r = decide_modeled_bps("AAPL")
    assert r.modeled_bps == SLIPPAGE_DEFAULT_BPS
    assert r.source == "default"
    assert r.measured_bps == pytest.approx(5.0)
    assert r.fill_count == 5


def test_policy_above_min_favorable_floors_at_default(fake_journal):
    # 25 favorable fills: measured = 0, but modeled floors at default
    fills = [_fill("AAPL", "buy", 100.0, 99.95) for _ in range(25)]
    _write_fills(fake_journal, fills)

    r = decide_modeled_bps("AAPL")
    assert r.modeled_bps == SLIPPAGE_DEFAULT_BPS
    assert r.source == "empirical"
    assert r.measured_bps == pytest.approx(0.0)
    assert r.fill_bias_bps == pytest.approx(5.0)  # favorable by 5 bps


def test_policy_above_min_unfavorable_uses_measured(fake_journal):
    # 25 fills at 3 bps cost → modeled = 3.0 (> default 2.0)
    fills = [_fill("AAPL", "buy", 100.0, 100.03) for _ in range(25)]
    _write_fills(fake_journal, fills)

    r = decide_modeled_bps("AAPL")
    assert r.modeled_bps == pytest.approx(3.0)
    assert r.source == "empirical"


def test_policy_window_cap(fake_journal):
    # 100 fills total: 60 old bad (10 bps) + 40 recent good (0 bps favorable).
    # Window of 50 = last 50 = 10 bad + 40 good → measured = (10*10 + 40*0)/50 = 2.0
    old = [_fill("AAPL", "buy", 100.0, 100.10) for _ in range(60)]
    new = [_fill("AAPL", "buy", 100.0,  99.90) for _ in range(40)]
    _write_fills(fake_journal, old + new)

    r = decide_modeled_bps("AAPL")
    assert r.fill_count == SLIPPAGE_WINDOW  # 50
    assert r.measured_bps == pytest.approx(2.0)


def test_policy_symbol_case_insensitive(fake_journal):
    fills = [_fill("AAPL", "buy", 100.0, 100.03) for _ in range(25)]
    _write_fills(fake_journal, fills)
    assert decide_modeled_bps("aapl").fill_count == 25


def test_policy_ignores_other_symbols(fake_journal):
    fills = [_fill("TSLA", "buy", 100.0, 100.10) for _ in range(30)]
    _write_fills(fake_journal, fills)
    r = decide_modeled_bps("AAPL")
    assert r.fill_count == 0
    assert r.source == "default"


def test_policy_skips_rows_missing_expected_price(fake_journal):
    # Legacy rows without expected_price must not crash or contribute.
    fills = [
        {"symbol": "AAPL", "side": "buy", "price": 100.10},  # no expected_price
        _fill("AAPL", "buy", 100.0, 100.03),
    ]
    _write_fills(fake_journal, fills)
    r = decide_modeled_bps("AAPL")
    assert r.fill_count == 1
    assert r.measured_bps == pytest.approx(3.0)
