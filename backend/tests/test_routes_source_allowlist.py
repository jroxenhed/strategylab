"""Tests for F94 — `source` allowlist on /api/ohlcv, /api/indicators, /api/backtest.

Mirrors F37 (quote routes). Each route should 400 on unknown sources BEFORE
fetch is reached, so a provider-enumeration probe can't observe timing or
error-shape side channels.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from main import app
from shared import get_available_providers


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _guard_fixture_name():
    """Test predicate: 'no_such_provider' must not collide with a real provider."""
    assert "no_such_provider" not in get_available_providers(), (
        "fixture name collides with a registered provider"
    )


def test_ohlcv_rejects_unknown_source(client, monkeypatch):
    """GET /api/ohlcv/AAPL?source=bogus → 400, _fetch never invoked."""
    from routes import data as data_mod

    def boom(*a, **k):
        raise AssertionError("_fetch must not be called on bad source")

    monkeypatch.setattr(data_mod, "_fetch", boom)
    resp = client.get("/api/ohlcv/AAPL", params={"source": "no_such_provider"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid source"


def test_indicators_rejects_unknown_source(client, monkeypatch):
    """POST /api/indicators/AAPL with source=bogus → 400 before fetch."""
    from routes import indicators as ind_mod

    def boom(*a, **k):
        raise AssertionError("_fetch must not be called on bad source")

    monkeypatch.setattr(ind_mod, "_fetch", boom)
    resp = client.post(
        "/api/indicators/AAPL",
        json={
            "source": "no_such_provider",
            "instances": [{"id": "x", "type": "rsi", "params": {}}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid source"


def test_backtest_rejects_unknown_source(client, monkeypatch):
    """POST /api/backtest with source=bogus → 400 before _fetch / regime work."""
    from routes import backtest as bt_mod

    def boom(*a, **k):
        raise AssertionError("_fetch must not be called on bad source")

    monkeypatch.setattr(bt_mod, "_fetch", boom)
    resp = client.post(
        "/api/backtest",
        json={
            "ticker": "AAPL",
            "buy_rules": [],
            "sell_rules": [],
            "source": "no_such_provider",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid source"


def test_ohlcv_default_yahoo_source_passes_allowlist(client, monkeypatch):
    """Sanity check: a known-good provider gets past the allowlist gate.

    Stubs _fetch with a tiny DataFrame so the route succeeds rather than
    hitting the network.
    """
    import pandas as pd
    from routes import data as data_mod

    df = pd.DataFrame(
        {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5], "Volume": [1000]},
        index=pd.DatetimeIndex(["2024-01-02"]),
    )

    def fake_fetch(*a, **k):
        return df

    monkeypatch.setattr(data_mod, "_fetch", fake_fetch)
    resp = client.get("/api/ohlcv/AAPL", params={"source": "yahoo"})
    assert resp.status_code == 200


def test_ohlcv_uppercase_source_normalizes_through(client, monkeypatch):
    """`source=YAHOO` (mixed/upper case) must normalize through `require_valid_source`
    rather than fall through to `_fetch`'s separate 'Unknown data source' branch.

    Pre-fix this would have escaped the F94 guard and hit _fetch's own 400 with a
    different detail string — a uniform-error-shape regression.
    """
    import pandas as pd
    from routes import data as data_mod

    captured = {}

    def fake_fetch(*a, **k):
        captured["source"] = k.get("source")
        return pd.DataFrame(
            {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
            index=pd.DatetimeIndex(["2024-01-02"]),
        )

    monkeypatch.setattr(data_mod, "_fetch", fake_fetch)
    resp = client.get("/api/ohlcv/AAPL", params={"source": "YAHOO"})
    assert resp.status_code == 200
    assert captured["source"] == "yahoo"


def test_indicators_default_source_passes_allowlist(client, monkeypatch):
    """Positive control for /api/indicators: known source returns 200, not 400."""
    import pandas as pd
    from routes import indicators as ind_mod

    df = pd.DataFrame(
        {"Open": [1.0] * 30, "High": [1.0] * 30, "Low": [1.0] * 30,
         "Close": [1.0] * 30, "Volume": [1] * 30},
        index=pd.date_range("2024-01-02", periods=30),
    )

    def fake_fetch(*a, **k):
        return df

    monkeypatch.setattr(ind_mod, "_fetch", fake_fetch)
    resp = client.post(
        "/api/indicators/AAPL",
        json={
            "source": "yahoo",
            "instances": [{"id": "rsi1", "type": "rsi", "params": {"period": 14}}],
        },
    )
    assert resp.status_code == 200


def test_backtest_default_source_passes_allowlist(client, monkeypatch):
    """Positive control for /api/backtest: known source returns 200, not 400.

    Catches an inverted condition (`if source IN providers: raise`) that
    would otherwise reject every legitimate request silently.
    """
    import pandas as pd
    from routes import backtest as bt_mod

    df = pd.DataFrame(
        {"Open": [100.0] * 5, "High": [101.0] * 5, "Low": [99.0] * 5,
         "Close": [100.0] * 5, "Volume": [1000] * 5},
        index=pd.date_range("2024-01-02", periods=5),
    )

    def fake_fetch(*a, **k):
        return df

    monkeypatch.setattr(bt_mod, "_fetch", fake_fetch)
    resp = client.post(
        "/api/backtest",
        json={
            "ticker": "AAPL",
            "buy_rules": [],
            "sell_rules": [],
            "source": "yahoo",
            "start": "2024-01-02",
            "end": "2024-01-06",
        },
    )
    # 200 means the F94 boundary check let it through; the backtest itself
    # runs against the stub frame and produces a valid response object.
    assert resp.status_code == 200
