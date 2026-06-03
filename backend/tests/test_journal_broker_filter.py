import json
import pytest
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from routes import trading as trading_mod


@pytest.fixture(autouse=True)
def clean_journal(tmp_path, monkeypatch):
    import journal
    fake = tmp_path / "trade_journal.json"
    monkeypatch.setattr(journal, "JOURNAL_PATH", fake)
    yield


def test_log_trade_stamps_broker():
    from journal import _log_trade, JOURNAL_PATH
    _log_trade("AAPL", "buy", 1, 100.0, source="bot",
               direction="long", bot_id="b1", broker="ibkr")
    rows = json.loads(JOURNAL_PATH.read_text())["trades"]
    assert rows[-1]["broker"] == "ibkr"


def test_log_trade_defaults_broker_to_null_for_manual():
    from journal import _log_trade, JOURNAL_PATH
    _log_trade("AAPL", "buy", 1, 100.0, source="manual", direction="long")
    rows = json.loads(JOURNAL_PATH.read_text())["trades"]
    assert rows[-1].get("broker") is None


# ---------------------------------------------------------------------------
# F217: GET /api/trading/journal returns total (pre-limit filtered count)
# ---------------------------------------------------------------------------

def _make_journal(path, trades: list):
    path.write_text(json.dumps({"trades": trades}))


def _trade(symbol="AAPL", broker=None):
    return {"symbol": symbol, "type": "buy", "shares": 1, "price": 100.0,
            "broker": broker}


@pytest.fixture()
def journal_client(tmp_path, monkeypatch):
    """TestClient with JOURNAL_PATH redirected to tmp_path."""
    fake = tmp_path / "trade_journal.json"
    monkeypatch.setattr(trading_mod, "JOURNAL_PATH", fake)
    with TestClient(app) as c:
        yield c, fake


def test_journal_total_with_limit(journal_client):
    """limit truncates trades → total == full filtered count, len(trades) == limit."""
    client, journal_path = journal_client
    trades = [_trade() for _ in range(10)]
    _make_journal(journal_path, trades)

    resp = client.get("/api/trading/journal", params={"limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10
    assert len(body["trades"]) == 3


def test_journal_total_no_limit(journal_client):
    """No limit → total == len(trades)."""
    client, journal_path = journal_client
    trades = [_trade() for _ in range(7)]
    _make_journal(journal_path, trades)

    resp = client.get("/api/trading/journal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 7
    assert len(body["trades"]) == 7
    assert body["total"] == len(body["trades"])


def test_journal_total_missing_file(tmp_path, monkeypatch):
    """Missing journal file → total == 0."""
    missing = tmp_path / "nonexistent.json"
    monkeypatch.setattr(trading_mod, "JOURNAL_PATH", missing)
    with TestClient(app) as client:
        resp = client.get("/api/trading/journal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["trades"] == []


def test_journal_total_after_broker_filter(journal_client):
    """Broker filter applied before total; limit after → total reflects filtered count."""
    client, journal_path = journal_client
    trades = [_trade(broker="ibkr")] * 8 + [_trade(broker="alpaca")] * 5
    _make_journal(journal_path, trades)

    resp = client.get("/api/trading/journal", params={"broker": "ibkr", "limit": 4})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 8
    assert len(body["trades"]) == 4
