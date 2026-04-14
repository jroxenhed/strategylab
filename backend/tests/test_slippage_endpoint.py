"""Tests for GET /api/slippage/{symbol}."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import pytest
from fastapi.testclient import TestClient
from main import app
import journal


@pytest.fixture
def fake_journal(tmp_path, monkeypatch):
    path = tmp_path / "trade_journal.json"
    monkeypatch.setattr(journal, "JOURNAL_PATH", path)
    import routes.slippage as slip_mod
    monkeypatch.setattr(slip_mod, "JOURNAL_PATH", path)

    def write(trades):
        path.write_text(json.dumps({"trades": trades}, indent=2))

    return write


def test_returns_null_when_no_data(fake_journal):
    fake_journal([])
    resp = TestClient(app).get("/api/slippage/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"empirical_pct": None, "fill_count": 0}


def test_filters_by_symbol(fake_journal):
    fake_journal([
        {"symbol": "AAPL", "side": "buy", "price": 100.1, "expected_price": 100.0},
        {"symbol": "MSFT", "side": "buy", "price": 200.2, "expected_price": 200.0},
    ])
    resp = TestClient(app).get("/api/slippage/AAPL")
    body = resp.json()
    assert body["fill_count"] == 1
    assert abs(body["empirical_pct"] - 0.1) < 1e-6


def test_signed_for_sell_and_short(fake_journal):
    fake_journal([
        {"symbol": "X", "side": "sell", "price": 99.0, "expected_price": 100.0},
        {"symbol": "X", "side": "short", "price": 99.0, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/X").json()
    assert abs(body["empirical_pct"] - 1.0) < 1e-6
    assert body["fill_count"] == 2


def test_includes_favorable_fills(fake_journal):
    fake_journal([
        {"symbol": "X", "side": "buy", "price": 99.0, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/X").json()
    assert body["empirical_pct"] < 0
    assert body["fill_count"] == 1


def test_skips_rows_missing_expected_price(fake_journal):
    fake_journal([
        {"symbol": "X", "side": "buy", "price": 100.0, "expected_price": None},
        {"symbol": "X", "side": "buy", "price": 101.0, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/X").json()
    assert body["fill_count"] == 1
    assert abs(body["empirical_pct"] - 1.0) < 1e-6


def test_symbol_case_insensitive(fake_journal):
    fake_journal([
        {"symbol": "AAPL", "side": "buy", "price": 100.1, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/aapl").json()
    assert body["fill_count"] == 1
