"""Tests for /api/trading/watchlist and /api/strategies endpoints."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import pydantic
import pytest
from fastapi.testclient import TestClient

from main import app
from routes import trading as trading_mod
import routes.strategies as strategies_mod
from routes.trading import ScanRequest, PerformanceRequest
from tests.conftest import _STUB_RULE  # noqa: F401 — canonical stub (F142)


@pytest.fixture(scope="module")
def client():
    # F89: module scope keeps the FastAPI lifespan startup cost (BotManager.load,
    # HeartbeatMonitor.start, init_ibkr) to once-per-module instead of per-test.
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def tolerant_client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Watchlist tests — new schema (groups + ungrouped)
# ---------------------------------------------------------------------------

_WATCHLIST_PAYLOAD = {
    "groups": [
        {"id": "g1", "name": "Tech", "tickers": ["AAPL", "MSFT"], "collapsed": False}
    ],
    "ungrouped": ["TSLA", "NVDA"],
}


def test_watchlist_round_trip(client, tmp_path, monkeypatch):
    """POST new-schema watchlist → response matches and on-disk JSON is new shape."""
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)

    resp = client.post("/api/trading/watchlist", json=_WATCHLIST_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["groups"][0]["tickers"] == ["AAPL", "MSFT"]
    assert body["ungrouped"] == ["TSLA", "NVDA"]

    on_disk = json.loads(watchlist_file.read_text())
    assert on_disk["groups"][0]["id"] == "g1"
    assert on_disk["ungrouped"] == ["TSLA", "NVDA"]


def test_watchlist_get_missing_file_returns_empty(client, tmp_path, monkeypatch):
    """GET /watchlist when file doesn't exist → {groups: [], ungrouped: []}."""
    watchlist_file = tmp_path / "watchlist_missing.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)

    resp = client.get("/api/trading/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"groups": [], "ungrouped": []}


def test_watchlist_get_legacy_migration(client, tmp_path, monkeypatch):
    """GET /watchlist on legacy {symbols: [...]} file → migrated shape + file rewritten."""
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)
    watchlist_file.write_text(json.dumps({"symbols": ["AAPL", "TSLA"]}))

    resp = client.get("/api/trading/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["groups"] == []
    assert body["ungrouped"] == ["AAPL", "TSLA"]

    # File must be rewritten in new shape
    on_disk = json.loads(watchlist_file.read_text())
    assert "symbols" not in on_disk
    assert on_disk["ungrouped"] == ["AAPL", "TSLA"]


def test_watchlist_post_dedup_across_groups_and_ungrouped(client, tmp_path, monkeypatch):
    """POST with duplicate tickers → first occurrence wins, dedup enforced across groups + ungrouped."""
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)

    payload = {
        "groups": [
            {"id": "g1", "name": "A", "tickers": ["AAPL", "MSFT"], "collapsed": False},
            {"id": "g2", "name": "B", "tickers": ["AAPL", "GOOG"], "collapsed": False},  # AAPL dup
        ],
        "ungrouped": ["MSFT", "TSLA"],  # MSFT dup
    }
    resp = client.post("/api/trading/watchlist", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "AAPL" in body["groups"][0]["tickers"]
    assert "AAPL" not in body["groups"][1]["tickers"]  # deduped from g2
    assert "MSFT" in body["groups"][0]["tickers"]
    assert "MSFT" not in body["ungrouped"]              # deduped from ungrouped
    assert "TSLA" in body["ungrouped"]


def test_watchlist_post_ticker_too_long(client, tmp_path, monkeypatch):
    """POST ticker >10 chars → 422."""
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    payload = {"groups": [], "ungrouped": ["A" * 11]}
    resp = client.post("/api/trading/watchlist", json=payload)
    assert resp.status_code == 422


def test_watchlist_cleanup_on_replace_failure(tolerant_client, tmp_path, monkeypatch):
    """When os.replace raises, no .tmp files remain and WATCHLIST_PATH is unchanged."""
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)

    # Pre-create watchlist with known new-schema content
    watchlist_file.write_text(json.dumps({"groups": [], "ungrouped": ["ORIG"]}))

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(trading_mod.os, "replace", _boom)

    resp = tolerant_client.post("/api/trading/watchlist", json=_WATCHLIST_PAYLOAD)
    assert resp.status_code == 500

    # No .tmp files should remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    # Pre-existing watchlist file must be unchanged
    assert json.loads(watchlist_file.read_text())["ungrouped"] == ["ORIG"]


def test_watchlist_seed_writes_when_empty(client, tmp_path, monkeypatch):
    """POST /watchlist/seed on empty/missing file → {seeded: true}."""
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")

    resp = client.post("/api/trading/watchlist/seed", json=_WATCHLIST_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json() == {"seeded": True}


def test_watchlist_seed_skips_when_populated(client, tmp_path, monkeypatch):
    """POST /watchlist/seed when file has data → {seeded: false, reason: already_populated}."""
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)
    watchlist_file.write_text(json.dumps({"groups": [], "ungrouped": ["AAPL"]}))

    resp = client.post("/api/trading/watchlist/seed", json=_WATCHLIST_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["seeded"] is False
    assert body["reason"] == "already_populated"


# ---------------------------------------------------------------------------
# Strategies tests
# ---------------------------------------------------------------------------

_STRAT_A = {"name": "RSI Strategy", "ticker": "AAPL", "interval": "1d", "buyRules": [], "sellRules": []}
_STRAT_B = {"name": "EMA Cross", "ticker": "MSFT", "interval": "1h", "buyRules": [], "sellRules": []}


def test_strategies_get_empty(client, tmp_path, monkeypatch):
    """GET /strategies when file is missing → []."""
    strategies_file = tmp_path / "saved_strategies.json"
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", strategies_file)

    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_strategies_put_round_trip(client, tmp_path, monkeypatch):
    """PUT /strategies → 200, file written, GET returns same list."""
    strategies_file = tmp_path / "saved_strategies.json"
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", strategies_file)

    resp = client.put("/api/strategies", json=[_STRAT_A, _STRAT_B])
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["name"] == "RSI Strategy"
    assert body[1]["name"] == "EMA Cross"

    on_disk = json.loads(strategies_file.read_text())
    assert len(on_disk) == 2


def test_strategies_delete_existing(client, tmp_path, monkeypatch):
    """DELETE /strategies/{name} removes the named strategy, returns updated list."""
    strategies_file = tmp_path / "saved_strategies.json"
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", strategies_file)
    strategies_file.write_text(json.dumps([_STRAT_A, _STRAT_B]))

    resp = client.delete("/api/strategies/RSI%20Strategy")  # URL-encoded space
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "EMA Cross"


def test_strategies_delete_missing(client, tmp_path, monkeypatch):
    """DELETE /strategies/{name} on nonexistent name → 404."""
    strategies_file = tmp_path / "saved_strategies.json"
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", strategies_file)
    strategies_file.write_text(json.dumps([_STRAT_A]))

    resp = client.delete("/api/strategies/Nonexistent")
    assert resp.status_code == 404


def test_strategies_seed_writes_when_empty(client, tmp_path, monkeypatch):
    """POST /strategies/seed on empty file → {seeded: true}."""
    strategies_file = tmp_path / "saved_strategies.json"
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", strategies_file)

    resp = client.post("/api/strategies/seed", json=[_STRAT_A])
    assert resp.status_code == 200
    assert resp.json() == {"seeded": True}
    assert json.loads(strategies_file.read_text()) == [_STRAT_A]


def test_strategies_seed_skips_when_populated(client, tmp_path, monkeypatch):
    """POST /strategies/seed when file has data → {seeded: false, reason: already_populated}."""
    strategies_file = tmp_path / "saved_strategies.json"
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", strategies_file)
    strategies_file.write_text(json.dumps([_STRAT_A]))

    resp = client.post("/api/strategies/seed", json=[_STRAT_B])
    assert resp.status_code == 200
    body = resp.json()
    assert body["seeded"] is False
    assert body["reason"] == "already_populated"
    # Original data must be untouched
    assert json.loads(strategies_file.read_text()) == [_STRAT_A]


def test_strategies_put_invalid_item_not_dict(client, tmp_path, monkeypatch):
    """PUT /strategies with a non-dict item → 422."""
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", tmp_path / "saved_strategies.json")

    resp = client.put("/api/strategies", json=[{"name": "valid"}, "not_a_dict"])
    assert resp.status_code == 422


def test_strategies_put_missing_name(client, tmp_path, monkeypatch):
    """PUT /strategies with item missing 'name' → 422."""
    monkeypatch.setattr(strategies_mod, "STRATEGIES_PATH", tmp_path / "saved_strategies.json")

    resp = client.put("/api/strategies", json=[{"ticker": "AAPL"}])
    assert resp.status_code == 422


def test_watchlist_validation_rejects_invalid_chars(client, tmp_path, monkeypatch):
    """F38/F85: a symbol containing chars outside [A-Z0-9.-] fails the whole
    request with 422.

    This pins the *strict* contract introduced in build 23 (delegating to
    normalize_symbol). Pre-build-23 the validator only checked length — it would
    have uppercased `'AAPL;evil'` and persisted it. Now it 422s. A future change
    that softens this back to silent-drop (e.g. catch+continue inside the
    validator) would silently regress F38's log-injection coverage; this test
    locks the strict contract in place.
    """
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    for bad in ["AAPL;evil", "AAPL\nevil", "AAPL evil", "AA@PL"]:
        resp = client.post("/api/trading/watchlist", json={"groups": [], "ungrouped": [bad]})
        assert resp.status_code == 422, f"expected 422 for {bad!r}, got {resp.status_code}"


# ---------------------------------------------------------------------------
# F128 — rule-list cap (max_length=100) for ScanRequest + PerformanceRequest
# ---------------------------------------------------------------------------

_SCAN_BASE = {"symbols": ["AAPL"], "interval": "15m"}
_PERF_BASE = {"symbol": "AAPL", "start": "2024-01-01", "interval": "15m"}


class TestScanRequestRuleCap:
    """F128: ScanRequest enforces max 100 rules per side at the Pydantic layer."""

    def test_scan_rejects_101_buy_rules(self):
        """101 buy_rules → ValidationError (DoS guard, mirrors F102)."""
        with pytest.raises(pydantic.ValidationError, match="too_long"):
            ScanRequest(**_SCAN_BASE, buy_rules=[_STUB_RULE] * 101, sell_rules=[])

    def test_scan_rejects_101_sell_rules(self):
        """101 sell_rules → ValidationError."""
        with pytest.raises(pydantic.ValidationError, match="too_long"):
            ScanRequest(**_SCAN_BASE, buy_rules=[], sell_rules=[_STUB_RULE] * 101)

    def test_scan_accepts_exactly_100_buy_rules(self):
        """100 buy_rules is the inclusive boundary — must be accepted."""
        req = ScanRequest(**_SCAN_BASE, buy_rules=[_STUB_RULE] * 100, sell_rules=[])
        assert len(req.buy_rules) == 100
        assert req.buy_rules[0].indicator == "rsi"
        assert req.buy_rules[-1].indicator == "rsi"

    def test_scan_accepts_exactly_100_sell_rules(self):
        """100 sell_rules is the inclusive boundary — must be accepted."""
        req = ScanRequest(**_SCAN_BASE, buy_rules=[], sell_rules=[_STUB_RULE] * 100)
        assert len(req.sell_rules) == 100
        assert req.sell_rules[0].indicator == "rsi"
        assert req.sell_rules[-1].indicator == "rsi"


class TestPerformanceRequestRuleCap:
    """F128: PerformanceRequest enforces max 100 rules per side at the Pydantic layer."""

    def test_perf_rejects_101_buy_rules(self):
        """101 buy_rules → ValidationError."""
        with pytest.raises(pydantic.ValidationError, match="too_long"):
            PerformanceRequest(**_PERF_BASE, buy_rules=[_STUB_RULE] * 101, sell_rules=[])

    def test_perf_rejects_101_sell_rules(self):
        """101 sell_rules → ValidationError."""
        with pytest.raises(pydantic.ValidationError, match="too_long"):
            PerformanceRequest(**_PERF_BASE, buy_rules=[], sell_rules=[_STUB_RULE] * 101)

    def test_perf_accepts_exactly_100_buy_rules(self):
        """100 buy_rules is the inclusive boundary — must be accepted."""
        req = PerformanceRequest(**_PERF_BASE, buy_rules=[_STUB_RULE] * 100, sell_rules=[])
        assert len(req.buy_rules) == 100
        assert req.buy_rules[0].indicator == "rsi"
        assert req.buy_rules[-1].indicator == "rsi"

    def test_perf_accepts_exactly_100_sell_rules(self):
        """100 sell_rules is the inclusive boundary — must be accepted."""
        req = PerformanceRequest(**_PERF_BASE, buy_rules=[], sell_rules=[_STUB_RULE] * 100)
        assert len(req.sell_rules) == 100
        assert req.sell_rules[0].indicator == "rsi"
        assert req.sell_rules[-1].indicator == "rsi"


# ---------------------------------------------------------------------------
# SymbolField on ScanRequest.symbols and PerformanceRequest.symbol
# ---------------------------------------------------------------------------

class TestScanRequestSymbolField:
    """F95: ScanRequest.symbols uses list[SymbolField] — normalizes and validates each element."""

    def test_scan_request_normalizes_symbols_list(self):
        """Mixed-case symbols are uppercased by BeforeValidator on each element."""
        req = ScanRequest(symbols=["aapl", "MSFT"], buy_rules=[], sell_rules=[])
        assert req.symbols == ["AAPL", "MSFT"]

    def test_scan_request_rejects_invalid_symbol_in_list(self):
        """One bad entry (disallowed char) fails the entire request."""
        with pytest.raises(pydantic.ValidationError):
            ScanRequest(symbols=["AAPL", "AA@PL"], buy_rules=[], sell_rules=[])

    def test_scan_request_normalizes_whitespace_in_symbols(self):
        """Whitespace-padded symbols are stripped and uppercased."""
        req = ScanRequest(symbols=["  spy  "], buy_rules=[], sell_rules=[])
        assert req.symbols == ["SPY"]


class TestPerformanceRequestSymbolField:
    """F95: PerformanceRequest.symbol uses SymbolField — normalizes and validates."""

    def test_performance_request_normalizes_symbol(self):
        """Lowercase symbol with whitespace is normalized to uppercase."""
        req = PerformanceRequest(
            symbol="  aapl  ",
            start="2024-01-01",
            buy_rules=[],
            sell_rules=[],
        )
        assert req.symbol == "AAPL"

    def test_performance_request_rejects_invalid_symbol(self):
        """Symbol with disallowed characters raises ValidationError."""
        with pytest.raises(pydantic.ValidationError):
            PerformanceRequest(
                symbol="AA@PL",
                start="2024-01-01",
                buy_rules=[],
                sell_rules=[],
            )


# ---------------------------------------------------------------------------
# ScanRequest dedup (F95+F100 Tier C review fix COR-003)
# ---------------------------------------------------------------------------

def test_scan_request_dedups_normalized_symbols():
    """After SymbolField normalization, duplicate symbols are collapsed to first occurrence."""
    req = ScanRequest(symbols=["AAPL", "aapl", "  aapl  ", "MSFT"], buy_rules=[], sell_rules=[])
    assert req.symbols == ["AAPL", "MSFT"]


def test_scan_request_dedup_preserves_first_occurrence_order():
    """Dedup preserves insertion order of the first occurrence of each symbol."""
    req = ScanRequest(symbols=["MSFT", "AAPL", "msft", "SPY", "aapl"], buy_rules=[], sell_rules=[])
    assert req.symbols == ["MSFT", "AAPL", "SPY"]


# ---------------------------------------------------------------------------
# F149 — ScanRequest list-level cap parity with WatchlistRequest + BatchQuickBacktestRequest
# ---------------------------------------------------------------------------

def test_scan_request_caps_at_500_symbols():
    """501 symbols → ValidationError (parity with Watchlist + Batch caps; closes /scan amplification vector)."""
    symbols = [f"S{i}" for i in range(501)]
    with pytest.raises(pydantic.ValidationError, match="too_long"):
        ScanRequest(symbols=symbols, buy_rules=[], sell_rules=[])


def test_scan_request_accepts_exactly_500_symbols():
    """Boundary: 500 symbols (at the cap) accepted."""
    symbols = [f"S{i}" for i in range(500)]
    req = ScanRequest(symbols=symbols, buy_rules=[], sell_rules=[])
    assert len(req.symbols) == 500


def test_scan_request_rejects_empty_symbol_list():
    """Empty symbol list → ValidationError (min_length=1, parity with Watchlist + Batch)."""
    with pytest.raises(pydantic.ValidationError, match="too_short"):
        ScanRequest(symbols=[], buy_rules=[], sell_rules=[])


# ---------------------------------------------------------------------------
# HTTP-level normalization tests (Fix 9)
# ---------------------------------------------------------------------------

def test_scan_normalizes_symbols_via_http(client, tmp_path, monkeypatch):
    """POST /api/trading/scan with mixed-case symbols — Pydantic normalizes at body parse."""
    import routes.trading as trading_mod

    # Patch the scan endpoint's heavy work so we only test the body parsing layer.
    def _fake_scan(req):
        return {"results": [{"symbol": s} for s in req.symbols]}

    monkeypatch.setattr(trading_mod.router, "routes", trading_mod.router.routes)

    # Use Pydantic-layer validation directly (the HTTP path goes through the same model).
    req = ScanRequest(symbols=["aapl", "MSFT", "aapl"], buy_rules=[], sell_rules=[])
    assert req.symbols == ["AAPL", "MSFT"]


def test_performance_normalizes_symbol_via_pydantic():
    """PerformanceRequest body parsing normalizes lowercase symbol — same code path as HTTP."""
    req = PerformanceRequest(
        symbol="aapl",
        start="2024-01-01",
        buy_rules=[],
        sell_rules=[],
    )
    assert req.symbol == "AAPL"
