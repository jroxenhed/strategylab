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
    assert body == [{"symbol": "", "price": None, "change_pct": None, "error": "invalid symbol"}]


def test_whitespace_only_normalizes_to_empty_then_null():
    """All-whitespace input strips to empty → null entry."""
    body = TestClient(app).post("/api/quotes", json=["   "]).json()
    assert body == [{"symbol": "", "price": None, "change_pct": None, "error": "invalid symbol"}]


def test_overlong_symbol_returns_null_entry():
    """Symbol > 20 chars (post-normalize) returns null entry, with the echoed symbol
    truncated to 20 chars (F45) so the response payload stays bounded."""
    long_sym = "A" * 25
    body = TestClient(app).post("/api/quotes", json=[long_sym]).json()
    assert body == [{"symbol": "A" * 20, "price": None, "change_pct": None, "error": "invalid symbol"}]


def test_lowercase_padded_symbol_normalized_then_fetched(stub_fetch):
    """`'  aapl  '` should normalize to 'AAPL' and proceed through get_quote() → real result.

    F72 added `response_model=list[QuoteResult]` so every entry has the full 4-field shape
    `{symbol, price, change_pct, error}`. On success, `error` is `None` (Pydantic serializes
    the default). Previously the dict-shaped response omitted `error` entirely on success.
    """
    body = TestClient(app).post("/api/quotes", json=["  aapl  "]).json()
    assert len(body) == 1
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["price"] == 101.0
    assert body[0]["change_pct"] == pytest.approx(1.0)
    assert body[0]["error"] is None


def test_fetch_exception_returns_error_field(monkeypatch):
    """Exception during get_quote() surfaces as a generic `error` field, not the raw
    exception message.

    F75 sanitizes the inner HTTPException(500) detail to a fixed string so provider
    internals (IBKR host:port, ib_insync log paths, yfinance URLs) can't leak via the
    batch endpoint. Any non-404 inner status maps to "fetch failed".
    """
    def boom(ticker, start, end, tf, source="yahoo"):
        raise RuntimeError("upstream timeout")
    monkeypatch.setattr(quote_mod, "_fetch", boom)
    body = TestClient(app).post("/api/quotes", json=["AAPL"]).json()
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["price"] is None
    assert body[0]["error"] == "fetch failed"
    # Original message must NOT leak.
    assert "upstream timeout" not in body[0]["error"]


def test_fetch_empty_exception_message_falls_back(monkeypatch):
    """Exception with empty message → `error` field is the fixed "fetch failed" string.

    Pre-F75 the empty-message branch fell back to "no data"; after F75 the message
    content is irrelevant — any non-404 inner status maps to the same sanitized
    string. This is intentional: the dropoff between "no data" and "fetch failed"
    can't be controlled by provider exception wording anymore.
    """
    def boom(ticker, start, end, tf, source="yahoo"):
        raise RuntimeError("")
    monkeypatch.setattr(quote_mod, "_fetch", boom)
    body = TestClient(app).post("/api/quotes", json=["AAPL"]).json()
    assert body[0]["symbol"] == "AAPL"
    assert body[0]["error"] == "fetch failed"


def test_no_data_dataframe_uses_404_detail(monkeypatch):
    """Empty DataFrame → get_quote raises HTTPException(404, ...) → detail is surfaced cleanly (no '404:' prefix)."""
    def empty_fetch(ticker, start, end, tf, source="yahoo"):
        return pd.DataFrame({"Close": []})
    monkeypatch.setattr(quote_mod, "_fetch", empty_fetch)
    body = TestClient(app).post("/api/quotes", json=["AAPL"]).json()
    assert body[0]["error"] == "No data for AAPL"
    assert "404" not in body[0]["error"]


def test_mixed_batch_validates_per_entry(stub_fetch):
    """Valid + invalid entries in one request — invalid entries get null, valid entries get fetched."""
    body = TestClient(app).post(
        "/api/quotes",
        json=["", "AAPL", "X" * 21],
    ).json()
    assert body[0] == {"symbol": "", "price": None, "change_pct": None, "error": "invalid symbol"}
    assert body[1]["symbol"] == "AAPL"
    assert body[1]["price"] == 101.0
    assert body[2] == {"symbol": "X" * 20, "price": None, "change_pct": None, "error": "invalid symbol"}


def test_invalid_source_rejected_on_single_quote():
    """F37: GET /api/quote/{ticker}?source=bogus → 400 'Invalid source' before fetch.

    Catches the provider-enumeration leak: without this guard, an attacker could
    fuzz `source` and observe timing/error-shape differences to learn which
    providers are registered. No fetch stub needed — the source check fires
    before _fetch is reached.
    """
    from shared import get_available_providers
    assert "no_such_provider" not in get_available_providers(), (
        "test fixture name collides with a real provider"
    )
    resp = TestClient(app).get("/api/quote/AAPL", params={"source": "no_such_provider"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid source"


def test_invalid_source_rejected_on_batch_quotes():
    """F37: POST /api/quotes?source=bogus → 400 up front, never returns 20 'no data' rows."""
    resp = TestClient(app).post(
        "/api/quotes",
        params={"source": "no_such_provider"},
        json=["AAPL"],
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid source"


def test_log_injection_ticker_rejected_on_single_quote():
    """F38/F98: characters outside the [A-Z0-9.-] allowlist on path-param ticker → 422.

    F98 promotes from 400 to 422 (consistent with Pydantic envelope) and switches
    to a structured detail object — no raw input echo, machine-readable error key.

    Note: Starlette URL-decodes `%3B` to `;` *before* the route handler runs,
    so the validator (not routing) is what produces the 422. The 422 is the
    intended outcome, but the path is "decoded → regex rejects" not "404 routing".
    """
    resp = TestClient(app).get("/api/quote/AAPL%3Bevil")  # %3B = ';'
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "invalid_symbol"
    assert "^[A-Z0-9]" in detail["reason"]


def test_log_injection_ticker_rejected_on_batch_quotes(monkeypatch):
    """F38: control chars in batch entries fail _normalize_symbol's regex →
    'invalid symbol' error, and the bad input never reaches _fetch()."""
    def boom_if_called(*a, **k):
        raise AssertionError("_fetch must not be called for an invalid symbol")
    monkeypatch.setattr(quote_mod, "_fetch", boom_if_called)
    body = TestClient(app).post("/api/quotes", json=["AAPL\nevil"]).json()
    assert body[0]["error"] == "invalid symbol"


# ---------------------------------------------------------------------------
# F98: structured invalid-symbol detail on GET /api/quote/{ticker}
# ---------------------------------------------------------------------------

def test_invalid_ticker_single_quote_structured_detail():
    """F98: GET /api/quote/{ticker} with bad chars returns 422 with structured detail."""
    resp = TestClient(app).get("/api/quote/BAD%21SYM")  # %21 = '!'
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["error"] == "invalid_symbol"
    assert "^[A-Z0-9]" in detail["reason"]


def test_invalid_ticker_single_quote_no_input_echo():
    """F98: the structured 422 response must not echo the raw malicious input."""
    resp = TestClient(app).get("/api/quote/AAPL%3Bevil")  # ';' via %3B
    assert resp.status_code == 422
    body_text = resp.text
    # Raw input (';evil') must not appear anywhere in the response
    assert "evil" not in body_text
    assert ";" not in body_text
