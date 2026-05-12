"""F62 — test that _load_trades() caches and invalidates correctly."""
import json
import pytest


@pytest.fixture(autouse=True)
def clean_journal(tmp_path, monkeypatch):
    import journal
    fake = tmp_path / "trade_journal.json"
    monkeypatch.setattr(journal, "JOURNAL_PATH", fake)
    # Reset cache state between tests
    monkeypatch.setattr(journal, "_journal_cache", None)
    monkeypatch.setattr(journal, "_journal_cache_key", None)
    yield


def test_load_trades_empty_when_no_file():
    from journal import _load_trades
    assert _load_trades() == []


def test_load_trades_returns_written_trades():
    from journal import _log_trade, _load_trades
    _log_trade("AAPL", "buy", 1, 100.0, source="bot", direction="long", bot_id="b1")
    trades = _load_trades()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "AAPL"


def test_load_trades_caches_and_does_not_reparse(tmp_path, monkeypatch):
    """Second call with identical mtime must return cache without re-reading."""
    import journal

    # Write a real trade so the file exists
    journal._log_trade("SPY", "buy", 2, 50.0, source="bot", direction="long", bot_id="b2")

    read_count = [0]
    original_read_text = journal.JOURNAL_PATH.__class__.read_text

    # Monkeypatch the read_text method on the Path instance via the module attr
    real_path = journal.JOURNAL_PATH

    class CountingPath:
        """Proxy that counts read_text calls."""
        def __init__(self, path):
            self._path = path

        def __getattr__(self, name):
            return getattr(self._path, name)

        def read_text(self, *args, **kwargs):
            read_count[0] += 1
            return self._path.read_text(*args, **kwargs)

    counting_path = CountingPath(real_path)
    monkeypatch.setattr(journal, "JOURNAL_PATH", counting_path)
    # Also reset cache so first call is a cold read
    monkeypatch.setattr(journal, "_journal_cache", None)
    monkeypatch.setattr(journal, "_journal_cache_key", None)

    first = journal._load_trades()
    second = journal._load_trades()

    assert first == second
    # Only one read_text call despite two _load_trades() calls
    assert read_count[0] == 1, f"expected 1 read, got {read_count[0]}"


def test_cache_invalidated_after_write():
    """After _log_trade(), _load_trades() must return the new trade."""
    from journal import _log_trade, _load_trades

    _log_trade("TSLA", "buy", 3, 200.0, source="bot", direction="long", bot_id="b3")
    before = len(_load_trades())

    _log_trade("TSLA", "sell", 3, 210.0, source="bot", direction="long", bot_id="b3")
    after = len(_load_trades())

    assert after == before + 1
