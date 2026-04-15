"""Tests for GET /api/slippage/{symbol} — new shape."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import pytest
from fastapi.testclient import TestClient

from main import app
import journal
import slippage as slip_mod


@pytest.fixture
def fake_journal(tmp_path, monkeypatch):
    """Point journal + slippage module at a temp file. Shape: {"trades": [...]}."""
    path = tmp_path / "trade_journal.json"
    monkeypatch.setattr(journal, "JOURNAL_PATH", path)
    monkeypatch.setattr(slip_mod, "JOURNAL_PATH", path)

    def write(trades):
        path.write_text(json.dumps({"trades": trades}, indent=2))

    return write


def _fill(symbol="AAPL", side="buy", expected=100.0, price=100.0):
    return {"symbol": symbol, "side": side, "price": price, "expected_price": expected}


def test_empty_journal_returns_default(fake_journal):
    fake_journal([])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body == {
        "modeled_bps":    2.0,
        "measured_bps":   None,
        "fill_bias_bps":  None,
        "fill_count":     0,
        "source":         "default",
    }


def test_below_min_fills_source_is_default(fake_journal):
    fake_journal([_fill(side="buy", expected=100.0, price=100.05) for _ in range(5)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["source"] == "default"
    assert body["modeled_bps"] == 2.0
    assert body["measured_bps"] == pytest.approx(5.0)
    assert body["fill_count"] == 5


def test_above_min_favorable_floors_at_default(fake_journal):
    fake_journal([_fill(side="buy", expected=100.0, price=99.95) for _ in range(25)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["source"] == "empirical"
    assert body["modeled_bps"] == 2.0
    assert body["measured_bps"] == pytest.approx(0.0)
    assert body["fill_bias_bps"] == pytest.approx(5.0)


def test_above_min_unfavorable_uses_measured(fake_journal):
    fake_journal([_fill(side="buy", expected=100.0, price=100.03) for _ in range(25)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["source"] == "empirical"
    assert body["modeled_bps"] == pytest.approx(3.0)
    assert body["fill_bias_bps"] == pytest.approx(-3.0)


def test_sell_side_sign_convention(fake_journal):
    fake_journal([_fill(side="sell", expected=100.0, price=99.90) for _ in range(25)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["measured_bps"] == pytest.approx(10.0)
    assert body["fill_bias_bps"] == pytest.approx(-10.0)


def test_symbol_case_insensitive(fake_journal):
    fake_journal([_fill(symbol="AAPL", side="buy", expected=100.0, price=100.05)])
    body = TestClient(app).get("/api/slippage/aapl").json()
    assert body["fill_count"] == 1


def test_skips_rows_missing_expected_price(fake_journal):
    fake_journal([
        {"symbol": "AAPL", "side": "buy", "price": 100.0, "expected_price": None},
        _fill(side="buy", expected=100.0, price=100.03),
    ])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["fill_count"] == 1
