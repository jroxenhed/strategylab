"""Tests for /api/backtest/quick* — covers F91 BatchQuickBacktestRequest length cap
and SymbolField parity with F69's watchlist validator.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.conftest import _STUB_RULE  # noqa: F401 — canonical stub (F142)


def _base_batch_body(**overrides):
    body = {
        "symbols": ["AAPL"],
        "interval": "1d",
        "buy_rules": [],
        "sell_rules": [],
    }
    body.update(overrides)
    return body


@pytest.fixture
def client():
    return TestClient(app)


def test_batch_rejects_more_than_500_symbols(client):
    """F91: POST 501 symbols → 422 with the custom 'too many symbols' message."""
    resp = client.post(
        "/api/backtest/quick/batch",
        json=_base_batch_body(symbols=[f"SYM{i}" for i in range(501)]),
    )
    assert resp.status_code == 422
    detail_str = str(resp.json().get("detail", ""))
    assert "too many symbols" in detail_str


def test_batch_accepts_exactly_500_symbols(monkeypatch, client):
    """F91 boundary: 500 symbols (at the cap) is accepted.

    Also asserts the stub actually intercepted the call by recording every
    ticker — without this, the test would pass even if monkeypatch silently
    fell through to the real _run_quick (which would return 500 error rows
    and still satisfy `len(results) == 500`). Catches the vacuous-test
    regression flagged by testing review on build 24.
    """
    from routes import backtest_quick as bq_mod

    received: list[str] = []

    def fake_run(req):
        received.append(req.ticker)
        return bq_mod.QuickBacktestResult(ticker=req.ticker)

    monkeypatch.setattr(bq_mod, "_run_quick", fake_run)
    resp = client.post(
        "/api/backtest/quick/batch",
        json=_base_batch_body(symbols=[f"S{i}" for i in range(500)]),
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 500
    assert received == [f"S{i}" for i in range(500)]


def test_batch_accepts_exactly_20_char_symbol(monkeypatch, client):
    """F91 boundary: a 20-character symbol (at the SymbolField regex cap) is accepted."""
    from routes import backtest_quick as bq_mod

    received: list[str] = []

    def fake_run(req):
        received.append(req.ticker)
        return bq_mod.QuickBacktestResult(ticker=req.ticker)

    monkeypatch.setattr(bq_mod, "_run_quick", fake_run)
    resp = client.post(
        "/api/backtest/quick/batch",
        json=_base_batch_body(symbols=["A" * 20]),
    )
    assert resp.status_code == 200
    assert received == ["A" * 20]


def test_batch_cap_does_not_suppress_per_symbol_errors(client):
    """F91 ordering invariant: at exactly-500 entries, a single invalid char
    must still 422 — the list-cap check (which passes at 500) must not
    short-circuit the per-symbol normalize_symbol step."""
    body = _base_batch_body(symbols=[f"S{i}" for i in range(499)] + ["A;B"])
    resp = client.post("/api/backtest/quick/batch", json=body)
    assert resp.status_code == 422
    # If the list-cap branch fired by mistake, the message would say "too many"
    assert "too many" not in str(resp.json().get("detail", "")).lower()


def test_batch_rejects_per_symbol_length(client):
    """F91: a 21-char symbol post-strip → 422."""
    resp = client.post(
        "/api/backtest/quick/batch",
        json=_base_batch_body(symbols=["A" * 21]),
    )
    assert resp.status_code == 422


def test_batch_rejects_invalid_chars(client):
    """F91/F38: a symbol containing chars outside [A-Z0-9.-] fails the whole request.

    Pre-F91 the route accepted `list[str]` raw, and an entry like 'AAPL;evil'
    would flow into `_run_quick` → `_fetch` and propagate into log sinks via
    the f-string error path. The strict normalize_symbol regex closes that.
    """
    for bad in ["AAPL;evil", "AAPL\nevil", "AA@PL", "AAPL evil"]:
        resp = client.post(
            "/api/backtest/quick/batch",
            json=_base_batch_body(symbols=[bad]),
        )
        assert resp.status_code == 422, f"expected 422 for {bad!r}, got {resp.status_code}"


def test_batch_strips_and_uppercases(monkeypatch, client):
    """F91: lowercase/padded symbols normalize cleanly, empties are dropped."""
    from routes import backtest_quick as bq_mod

    received = []

    def fake_run(req):
        received.append(req.ticker)
        return bq_mod.QuickBacktestResult(ticker=req.ticker)

    monkeypatch.setattr(bq_mod, "_run_quick", fake_run)
    resp = client.post(
        "/api/backtest/quick/batch",
        json=_base_batch_body(symbols=["  aapl ", "msft", ""]),
    )
    assert resp.status_code == 200
    assert received == ["AAPL", "MSFT"]


def test_batch_rejects_all_empty_symbols(client):
    """F91: a list of pure whitespace/empties post-clean has zero entries → 422.

    Mirrors WatchlistRequest behaviour: empties are dropped silently, but the
    request must contain at least one valid symbol or it's an explicit error.
    """
    resp = client.post(
        "/api/backtest/quick/batch",
        json=_base_batch_body(symbols=["", "   "]),
    )
    assert resp.status_code == 422


def test_quick_single_ticker_rejects_invalid_chars(client):
    """F91/F38: same SymbolField on QuickBacktestRequest.ticker."""
    body = {
        "ticker": "AAPL;evil",
        "buy_rules": [],
        "sell_rules": [],
    }
    resp = client.post("/api/backtest/quick", json=body)
    assert resp.status_code == 422


def test_quick_single_ticker_normalizes(monkeypatch, client):
    """F91: lowercase ticker uppercases through SymbolField before _run_quick."""
    from routes import backtest_quick as bq_mod

    received = []

    def fake_run(req):
        received.append(req.ticker)
        return bq_mod.QuickBacktestResult(ticker=req.ticker)

    monkeypatch.setattr(bq_mod, "_run_quick", fake_run)
    resp = client.post(
        "/api/backtest/quick",
        json={"ticker": "  brk.b ", "buy_rules": [], "sell_rules": []},
    )
    assert resp.status_code == 200
    assert received == ["BRK.B"]


def test_quick_rejects_more_than_100_buy_rules(client):
    """F102: >100 buy_rules → 422. Same DoS class as F86 body cap — without
    the rule-list cap, the 1 MB body still admits thousands of small rules
    driving O(n_rules × n_bars) compute per backtest."""
    body = {
        "ticker": "AAPL",
        "buy_rules": [_STUB_RULE] * 101,
        "sell_rules": [],
    }
    resp = client.post("/api/backtest/quick", json=body)
    assert resp.status_code == 422


def test_quick_rejects_more_than_100_sell_rules(client):
    """F102: >100 sell_rules → 422 (same cap on the sell side)."""
    body = {
        "ticker": "AAPL",
        "buy_rules": [],
        "sell_rules": [_STUB_RULE] * 101,
    }
    resp = client.post("/api/backtest/quick", json=body)
    assert resp.status_code == 422


def test_quick_accepts_exactly_100_rules(monkeypatch, client):
    """F102 boundary: exactly 100 rules per side is accepted (cap is inclusive)."""
    from routes import backtest_quick as bq_mod

    def fake_run(req):
        return bq_mod.QuickBacktestResult(ticker=req.ticker)

    monkeypatch.setattr(bq_mod, "_run_quick", fake_run)
    body = {
        "ticker": "AAPL",
        "buy_rules": [_STUB_RULE] * 100,
        "sell_rules": [_STUB_RULE] * 100,
    }
    resp = client.post("/api/backtest/quick", json=body)
    assert resp.status_code == 200


def test_batch_rejects_more_than_100_buy_rules(client):
    """F102: rule-list cap applies to BatchQuickBacktestRequest too."""
    body = _base_batch_body(buy_rules=[_STUB_RULE] * 101)
    resp = client.post("/api/backtest/quick/batch", json=body)
    assert resp.status_code == 422


def test_batch_rejects_more_than_100_sell_rules(client):
    """F102: >100 sell_rules on batch endpoint → 422 (mirrors quick-endpoint sell-side test)."""
    body = _base_batch_body(sell_rules=[_STUB_RULE] * 101)
    resp = client.post("/api/backtest/quick/batch", json=body)
    assert resp.status_code == 422


def test_batch_accepts_exactly_100_rules(monkeypatch, client):
    """F102 boundary: 100 rules per side on the batch endpoint is accepted.

    Independent of the quick-endpoint boundary test — the cap lives on
    `BatchQuickBacktestRequest`'s own Field, so an off-by-one regression here
    would not be caught by `test_quick_accepts_exactly_100_rules`.
    """
    from routes import backtest_quick as bq_mod

    def fake_run(req):
        return bq_mod.QuickBacktestResult(ticker=req.ticker)

    monkeypatch.setattr(bq_mod, "_run_quick", fake_run)
    body = _base_batch_body(
        buy_rules=[_STUB_RULE] * 100,
        sell_rules=[_STUB_RULE] * 100,
    )
    resp = client.post("/api/backtest/quick/batch", json=body)
    assert resp.status_code == 200
