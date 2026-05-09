"""Tests for POST /api/quotes — covers F29 per-symbol validation/normalization."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import app
from routes import quote as quote_mod


@pytest.fixture
def stub_fetch(monkeypatch):
    """Substitute routes.quote._fetch with a stub returning a tiny valid OHLCV frame."""
    def fake_fetch(ticker, start, end, tf, source="yahoo"):
        return pd.DataFrame({"Close": [100.0, 101.0]})
    monkeypatch.setattr(quote_mod, "_fetch", fake_fetch)


def test_empty_string_returns_null_entry_without_fetch():
    """Empty string short-circuits to a null entry — `_fetch` is never invoked."""
    body = TestClient(app).post("/api/quotes", json=[""]).json()
    assert body == [{"symbol": "", "price": None, "change_pct": None}]


def test_whitespace_only_normalizes_to_empty_then_null():
    """All-whitespace input strips to empty → null entry."""
    body = TestClient(app).post("/api/quotes", json=["   "]).json()
    assert body == [{"symbol": "", "price": None, "change_pct": None}]


def test_overlong_symbol_returns_null_entry():
    """Symbol > 20 chars (post-normalize) returns null entry, echoing the normalized symbol back."""
    long_sym = "A" * 25
    body = TestClient(app).post("/api/quotes", json=[long_sym]).json()
    assert body == [{"symbol": long_sym, "price": None, "change_pct": None}]


def test_lowercase_padded_symbol_normalized_then_fetched(stub_fetch):
    """`'  aapl  '` should normalize to 'AAPL' and proceed through get_quote() → real result."""
    body = TestClient(app).post("/api/quotes", json=["  aapl  "]).json()
    assert len(body) == 1
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["price"] == 101.0
    assert body[0]["change_pct"] == pytest.approx(1.0)


def test_mixed_batch_validates_per_entry(stub_fetch):
    """Valid + invalid entries in one request — invalid entries get null, valid entries get fetched."""
    body = TestClient(app).post(
        "/api/quotes",
        json=["", "AAPL", "X" * 21],
    ).json()
    assert body[0] == {"symbol": "", "price": None, "change_pct": None}
    assert body[1]["symbol"] == "AAPL"
    assert body[1]["price"] == 101.0
    assert body[2] == {"symbol": "X" * 21, "price": None, "change_pct": None}
