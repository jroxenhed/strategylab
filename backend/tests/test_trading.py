"""Tests for POST /api/trading/watchlist — covers F69 length caps and F52 atomic writes."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import pytest
from fastapi.testclient import TestClient

from main import app
from routes import trading as trading_mod


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def tolerant_client():
    return TestClient(app, raise_server_exceptions=False)


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
