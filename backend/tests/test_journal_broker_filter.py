import json
import pytest


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
