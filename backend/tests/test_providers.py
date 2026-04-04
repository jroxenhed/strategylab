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
