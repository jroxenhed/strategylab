from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd


def test_yahoo_provider_returns_dataframe(monkeypatch):
    """YahooProvider.fetch() returns a DataFrame with the expected columns."""
    fake_df = pd.DataFrame({
        "Open": [100.0], "High": [105.0], "Low": [99.0],
        "Close": [103.0], "Volume": [1000000],
    }, index=pd.to_datetime(["2024-01-02"]))

    import yfinance as yf
    class FakeTicker:
        def history(self, **kwargs):
            return fake_df
    monkeypatch.setattr(yf, "Ticker", lambda symbol: FakeTicker())

    from shared import YahooProvider
    provider = YahooProvider()
    result = provider.fetch("AAPL", "2024-01-01", "2024-01-03", "1d")

    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(result) == 1
    assert result["Close"].iloc[0] == 103.0


def test_yahoo_provider_raises_on_empty(monkeypatch):
    """YahooProvider.fetch() raises HTTPException when no data returned."""
    import yfinance as yf
    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame()
    monkeypatch.setattr(yf, "Ticker", lambda symbol: FakeTicker())

    from shared import YahooProvider
    from fastapi import HTTPException
    import pytest

    provider = YahooProvider()
    with pytest.raises(HTTPException) as exc_info:
        provider.fetch("FAKE", "2024-01-01", "2024-01-03", "1d")
    assert exc_info.value.status_code == 404


def test_alpaca_provider_returns_dataframe(monkeypatch):
    """AlpacaProvider.fetch() returns a DataFrame with the expected columns."""
    fake_bars = {
        "AAPL": [
            type("Bar", (), {
                "timestamp": pd.Timestamp("2024-01-02", tz="UTC"),
                "open": 100.0, "high": 105.0, "low": 99.0,
                "close": 103.0, "volume": 1000000,
            })()
        ]
    }

    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")

    import shared
    # Mock the Alpaca client
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def get_stock_bars(self, request):
            return fake_bars

    monkeypatch.setattr(shared, "_create_alpaca_client", lambda: FakeClient())

    from shared import AlpacaProvider
    provider = AlpacaProvider(FakeClient())
    result = provider.fetch("AAPL", "2024-01-01", "2024-01-03", "1d")

    assert isinstance(result, pd.DataFrame)
    assert set(result.columns) == {"Open", "High", "Low", "Close", "Volume"}
    assert len(result) == 1
    assert result["Close"].iloc[0] == 103.0


def test_alpaca_provider_raises_on_empty(monkeypatch):
    """AlpacaProvider.fetch() raises HTTPException when no data returned."""
    class FakeClient:
        def get_stock_bars(self, request):
            return {"AAPL": []}

    from shared import AlpacaProvider
    from fastapi import HTTPException
    import pytest

    provider = AlpacaProvider(FakeClient())
    with pytest.raises(HTTPException) as exc_info:
        provider.fetch("FAKE", "2024-01-01", "2024-01-03", "1d")
    assert exc_info.value.status_code == 404


def test_alpaca_provider_rejects_unsupported_interval():
    """AlpacaProvider.fetch() raises 400 for intervals Alpaca doesn't support."""
    class FakeClient:
        pass

    from shared import AlpacaProvider
    from fastapi import HTTPException
    import pytest

    provider = AlpacaProvider(FakeClient())
    with pytest.raises(HTTPException) as exc_info:
        provider.fetch("AAPL", "2024-01-01", "2024-01-03", "2m")
    assert exc_info.value.status_code == 400
    assert "not supported by Alpaca" in exc_info.value.detail


def test_fetch_rejects_unknown_source():
    """_fetch() raises 400 for an unknown source name."""
    from shared import _fetch
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        _fetch("AAPL", "2024-01-01", "2024-01-03", "1d", source="nonexistent")
    assert exc_info.value.status_code == 400
    assert "Unknown data source" in exc_info.value.detail


from fastapi.testclient import TestClient


def test_providers_endpoint_includes_yahoo():
    """GET /api/providers always includes yahoo."""
    from main import app
    client = TestClient(app)
    resp = client.get("/api/providers")
    assert resp.status_code == 200
    assert "yahoo" in resp.json()["providers"]
