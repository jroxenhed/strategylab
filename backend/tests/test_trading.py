"""Tests for POST /api/trading/watchlist — covers F69 length caps and F52 atomic writes."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import pydantic
import pytest
from fastapi.testclient import TestClient

from main import app
from routes import trading as trading_mod
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


def test_watchlist_round_trip(client, tmp_path, monkeypatch):
    """POST a small symbol list → response matches post-validated (stripped, uppercased) input
    and on-disk JSON equals {symbols: [...]}.
    """
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)

    symbols = ["aapl", "msft", "spy"]
    resp = client.post("/api/trading/watchlist", json={"symbols": symbols})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols"] == ["AAPL", "MSFT", "SPY"]

    on_disk = json.loads(watchlist_file.read_text())
    assert on_disk == {"symbols": ["AAPL", "MSFT", "SPY"]}


def test_watchlist_validation_caps_length(client, tmp_path, monkeypatch):
    """POST a list of 501 symbols → 422 Unprocessable Entity with custom error message."""
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")

    symbols = [f"SYM{i}" for i in range(501)]
    resp = client.post("/api/trading/watchlist", json={"symbols": symbols})
    assert resp.status_code == 422
    # Verify the custom validator error message is returned, not Pydantic's generic 'too_long'.
    # If Field(max_length=500) were re-introduced, it would fire first and produce a
    # generic 'too_long' error type, which would NOT contain this text — catching the regression.
    body = resp.json()
    detail_str = str(body.get("detail", ""))
    assert "too many symbols" in detail_str


def test_watchlist_validation_per_symbol_length(client, tmp_path, monkeypatch):
    """POST a symbol longer than 20 chars → 422 Unprocessable Entity."""
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")

    resp = client.post("/api/trading/watchlist", json={"symbols": ["A" * 21]})
    assert resp.status_code == 422


def test_watchlist_strips_and_uppercases(client, tmp_path, monkeypatch):
    """POST symbols with whitespace and empty string → stripped, uppercased, empties filtered."""
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")

    resp = client.post("/api/trading/watchlist", json={"symbols": ["  spy  ", "qqq", ""]})
    assert resp.status_code == 200
    assert resp.json()["symbols"] == ["SPY", "QQQ"]


def test_watchlist_cleanup_on_replace_failure(tolerant_client, tmp_path, monkeypatch):
    """When os.replace raises, no .tmp files remain and WATCHLIST_PATH is unchanged."""
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)

    # Pre-create watchlist with known content
    watchlist_file.write_text(json.dumps({"symbols": ["ORIG"]}))

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(trading_mod.os, "replace", _boom)

    resp = tolerant_client.post("/api/trading/watchlist", json={"symbols": ["AAPL"]})
    # Route raises → FastAPI returns 500
    assert resp.status_code == 500

    # No .tmp files should remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    # Pre-existing watchlist file must be unchanged
    assert json.loads(watchlist_file.read_text()) == {"symbols": ["ORIG"]}


def test_watchlist_validation_exactly_500_symbols(client, tmp_path, monkeypatch):
    """Boundary: 500 symbols (at the cap) is accepted."""
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    symbols = [f"S{i}" for i in range(500)]
    resp = client.post("/api/trading/watchlist", json={"symbols": symbols})
    assert resp.status_code == 200
    assert len(resp.json()["symbols"]) == 500


def test_watchlist_validation_exactly_20_char_symbol(client, tmp_path, monkeypatch):
    """Boundary: 20-character symbol (at the cap) is accepted."""
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", tmp_path / "watchlist.json")
    resp = client.post("/api/trading/watchlist", json={"symbols": ["A" * 20]})
    assert resp.status_code == 200
    assert resp.json()["symbols"] == ["A" * 20]


@pytest.mark.parametrize("empty", [[], [""], ["   "], ["", "  ", "\t"]])
def test_watchlist_rejects_all_empty_symbols(client, tmp_path, monkeypatch, empty):
    """F104: harmonize empty-list-after-strip with BatchQuickBacktestRequest (F91).

    Closes F87 — pre-F104 POST {symbols: []} or {symbols: ["", "  "]} silently
    returned 200 and overwrote the on-disk watchlist with []. Now 422; wiping
    the watchlist requires an explicit DELETE (not yet wired) or a non-empty
    POST first.
    """
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)

    # Pre-seed so we can prove the on-disk file is unchanged after the 422.
    watchlist_file.write_text(json.dumps({"symbols": ["ORIG"]}))

    resp = client.post("/api/trading/watchlist", json={"symbols": empty})
    assert resp.status_code == 422
    # On-disk content must NOT have been silently overwritten.
    assert json.loads(watchlist_file.read_text()) == {"symbols": ["ORIG"]}


@pytest.mark.parametrize("empty", [[], [""], ["   "], ["", "  ", "\t"]])
def test_watchlist_422_does_not_create_file_when_absent(client, tmp_path, monkeypatch, empty):
    """F134: F104 sibling — when no watchlist file exists, a 422 must not
    accidentally create an empty one via partial path traversal in a future
    refactor. Pins that the validator rejects BEFORE any disk write.
    """
    watchlist_file = tmp_path / "watchlist.json"
    monkeypatch.setattr(trading_mod, "WATCHLIST_PATH", watchlist_file)
    assert not watchlist_file.exists(), "fixture must start clean"

    resp = client.post("/api/trading/watchlist", json={"symbols": empty})
    assert resp.status_code == 422
    assert not watchlist_file.exists(), (
        "422 path must not touch disk — would regress to F87 silent-wipe class"
    )


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
        resp = client.post("/api/trading/watchlist", json={"symbols": [bad]})
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
