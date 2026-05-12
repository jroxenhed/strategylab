"""Tests for `BotState.append_slippage_bps` (F56/F119) and
`BotState.append_equity_snapshot` (F58/F122) — pins the cap-at-N behaviour
and the rounding/shape contract added when the helpers were extracted.

These were filed as F121 (slippage cap) and F123 (equity snapshot cap)
after the F119 / F122 bundles routed call sites through the helpers without
direct unit coverage. Cap regression would silently let `BotState` grow
unbounded, which is exactly the bug the helpers were created to prevent.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from datetime import datetime
from pydantic import ValidationError
from bot_manager import BotState, BotConfig
from tests.conftest import _STUB_RULE  # noqa: F401 — canonical stub (F142)


def test_append_slippage_bps_caps_at_1000():
    """F121: 1001 appends → length 1000, first sample dropped, last preserved."""
    state = BotState()
    for i in range(1001):
        state.append_slippage_bps(float(i))
    assert len(state.slippage_bps) == 1000
    # The oldest entry (0.0) was evicted; the newest (1000.0) is the tail.
    assert state.slippage_bps[-1] == 1000.0
    # The first surviving sample is the second one ever appended (1.0 → 1.00).
    assert state.slippage_bps[0] == 1.0


def test_append_slippage_bps_rounds_to_two_decimals():
    """F121: helper rounds to 2 dp so JSON payloads stay compact."""
    state = BotState()
    state.append_slippage_bps(3.14159)
    state.append_slippage_bps(2.71828)
    assert state.slippage_bps == [3.14, 2.72]


def test_append_slippage_bps_below_cap_is_passthrough():
    """Sanity: append until the cap-1 boundary, all entries preserved in order."""
    state = BotState()
    for i in range(500):
        state.append_slippage_bps(float(i))
    assert len(state.slippage_bps) == 500
    assert state.slippage_bps[0] == 0.0
    assert state.slippage_bps[-1] == 499.0


def test_append_equity_snapshot_caps_at_500():
    """F123: 501 appends → length 500, first dropped, last preserved."""
    state = BotState()
    for i in range(501):
        state.append_equity_snapshot(float(i))
    assert len(state.equity_snapshots) == 500
    assert state.equity_snapshots[-1]["value"] == 500.0
    # First-survivor is the second append (value=1.0).
    assert state.equity_snapshots[0]["value"] == 1.0


def test_append_equity_snapshot_shape_and_rounding():
    """F123: each snapshot is `{"time": ISO-8601 UTC, "value": float rounded to 2 dp}`."""
    state = BotState()
    state.append_equity_snapshot(1234.5678)
    snap = state.equity_snapshots[-1]
    assert snap.keys() == {"time", "value"}
    assert snap["value"] == 1234.57
    # ISO-8601 with UTC offset; datetime.fromisoformat parses both ±HH:MM and "+00:00"
    parsed = datetime.fromisoformat(snap["time"])
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0.0


def test_append_equity_snapshot_below_cap_preserves_order():
    """Sanity: appends below the cap retain their full history in chronological order."""
    state = BotState()
    for i in range(250):
        state.append_equity_snapshot(float(i))
    assert len(state.equity_snapshots) == 250
    assert [s["value"] for s in state.equity_snapshots[:3]] == [0.0, 1.0, 2.0]
    assert state.equity_snapshots[-1]["value"] == 249.0


# ---------------------------------------------------------------------------
# F128: BotConfig buy_rules / sell_rules / long_* / short_* max_length=100
# ---------------------------------------------------------------------------

_BASE_CONFIG: dict = {
    "strategy_name": "test",
    "symbol": "SPY",
    "interval": "5m",
    "buy_rules": [],
    "sell_rules": [],
    "allocated_capital": 1000.0,
}


class TestBotConfigRuleCaps:
    """F128: BotConfig enforces max_length=100 on all six Rule list fields."""

    # -- buy_rules ------------------------------------------------------------

    def test_buy_rules_accepts_exactly_100(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "buy_rules": [_STUB_RULE] * 100})
        assert len(cfg.buy_rules) == 100
        assert cfg.buy_rules[0].indicator == "rsi"
        assert cfg.buy_rules[-1].indicator == "rsi"

    def test_buy_rules_rejects_101(self):
        with pytest.raises(ValidationError, match="too_long"):
            BotConfig(**{**_BASE_CONFIG, "buy_rules": [_STUB_RULE] * 101})

    # -- sell_rules -----------------------------------------------------------

    def test_sell_rules_accepts_exactly_100(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "sell_rules": [_STUB_RULE] * 100})
        assert len(cfg.sell_rules) == 100
        assert cfg.sell_rules[0].indicator == "rsi"
        assert cfg.sell_rules[-1].indicator == "rsi"

    def test_sell_rules_rejects_101(self):
        with pytest.raises(ValidationError, match="too_long"):
            BotConfig(**{**_BASE_CONFIG, "sell_rules": [_STUB_RULE] * 101})

    # -- long_buy_rules -------------------------------------------------------

    def test_long_buy_rules_accepts_none(self):
        cfg = BotConfig(**_BASE_CONFIG)
        assert cfg.long_buy_rules is None

    def test_long_buy_rules_accepts_empty(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "long_buy_rules": []})
        assert cfg.long_buy_rules == []

    def test_long_buy_rules_accepts_exactly_100(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "long_buy_rules": [_STUB_RULE] * 100})
        assert len(cfg.long_buy_rules) == 100
        assert cfg.long_buy_rules[0].indicator == "rsi"
        assert cfg.long_buy_rules[-1].indicator == "rsi"

    def test_long_buy_rules_rejects_101(self):
        with pytest.raises(ValidationError, match="too_long"):
            BotConfig(**{**_BASE_CONFIG, "long_buy_rules": [_STUB_RULE] * 101})

    # -- long_sell_rules ------------------------------------------------------

    def test_long_sell_rules_accepts_none(self):
        cfg = BotConfig(**_BASE_CONFIG)
        assert cfg.long_sell_rules is None

    def test_long_sell_rules_accepts_empty(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "long_sell_rules": []})
        assert cfg.long_sell_rules == []

    def test_long_sell_rules_accepts_exactly_100(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "long_sell_rules": [_STUB_RULE] * 100})
        assert len(cfg.long_sell_rules) == 100
        assert cfg.long_sell_rules[0].indicator == "rsi"
        assert cfg.long_sell_rules[-1].indicator == "rsi"

    def test_long_sell_rules_rejects_101(self):
        with pytest.raises(ValidationError, match="too_long"):
            BotConfig(**{**_BASE_CONFIG, "long_sell_rules": [_STUB_RULE] * 101})

    # -- short_buy_rules ------------------------------------------------------

    def test_short_buy_rules_accepts_none(self):
        cfg = BotConfig(**_BASE_CONFIG)
        assert cfg.short_buy_rules is None

    def test_short_buy_rules_accepts_empty(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "short_buy_rules": []})
        assert cfg.short_buy_rules == []

    def test_short_buy_rules_accepts_exactly_100(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "short_buy_rules": [_STUB_RULE] * 100})
        assert len(cfg.short_buy_rules) == 100
        assert cfg.short_buy_rules[0].indicator == "rsi"
        assert cfg.short_buy_rules[-1].indicator == "rsi"

    def test_short_buy_rules_rejects_101(self):
        with pytest.raises(ValidationError, match="too_long"):
            BotConfig(**{**_BASE_CONFIG, "short_buy_rules": [_STUB_RULE] * 101})

    # -- short_sell_rules -----------------------------------------------------

    def test_short_sell_rules_accepts_none(self):
        cfg = BotConfig(**_BASE_CONFIG)
        assert cfg.short_sell_rules is None

    def test_short_sell_rules_accepts_empty(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "short_sell_rules": []})
        assert cfg.short_sell_rules == []

    def test_short_sell_rules_accepts_exactly_100(self):
        cfg = BotConfig(**{**_BASE_CONFIG, "short_sell_rules": [_STUB_RULE] * 100})
        assert len(cfg.short_sell_rules) == 100
        assert cfg.short_sell_rules[0].indicator == "rsi"
        assert cfg.short_sell_rules[-1].indicator == "rsi"

    def test_short_sell_rules_rejects_101(self):
        with pytest.raises(ValidationError, match="too_long"):
            BotConfig(**{**_BASE_CONFIG, "short_sell_rules": [_STUB_RULE] * 101})


# ---------------------------------------------------------------------------
# BotManager.load() migration: normalize + granular error handling
# ---------------------------------------------------------------------------

import json as _json
import logging as _logging
import bot_manager as _bot_manager_mod
from bot_manager import BotManager


def _make_bot_entry(bot_id: str, symbol: str) -> dict:
    """Minimal bots.json entry with only required BotConfig fields."""
    return {
        "config": {
            "bot_id": bot_id,
            "strategy_name": "Test",
            "symbol": symbol,
            "interval": "5m",
            "buy_rules": [],
            "sell_rules": [],
            "allocated_capital": 100.0,
        },
        "state": {},
    }


class TestBotManagerLoadMigration:
    """F100: BotManager.load() normalizes symbols and skips-with-warning on invalid ones."""

    def _write_bots_json(self, path, entries: list[dict], bot_fund: float = 0.0):
        path.write_text(_json.dumps({"bot_fund": bot_fund, "bots": entries}))

    def test_load_normalizes_lowercase_symbol(self, tmp_path, monkeypatch):
        """A lowercase symbol in bots.json is auto-normalized to uppercase on load."""
        bots_file = tmp_path / "bots.json"
        self._write_bots_json(bots_file, [_make_bot_entry("bot-1", "aapl")])
        monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(bots_file))

        mgr = BotManager()
        mgr.load()

        assert "bot-1" in mgr.bots
        config, _ = mgr.bots["bot-1"]
        assert config.symbol == "AAPL"

    def test_load_normalizes_whitespace_symbol(self, tmp_path, monkeypatch):
        """Whitespace-padded symbol is stripped and uppercased."""
        bots_file = tmp_path / "bots.json"
        self._write_bots_json(bots_file, [_make_bot_entry("bot-2", "  msft  ")])
        monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(bots_file))

        mgr = BotManager()
        mgr.load()

        assert "bot-2" in mgr.bots
        config, _ = mgr.bots["bot-2"]
        assert config.symbol == "MSFT"

    def test_load_skips_invalid_symbol_with_warning(self, tmp_path, monkeypatch, caplog):
        """An invalid symbol (disallowed chars) is skipped and logged at WARNING level."""
        bots_file = tmp_path / "bots.json"
        self._write_bots_json(bots_file, [_make_bot_entry("bad-bot", "AA@PL")])
        monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(bots_file))

        mgr = BotManager()
        with caplog.at_level(_logging.WARNING, logger="bot_manager"):
            mgr.load()

        assert "bad-bot" not in mgr.bots
        assert len(mgr.bots) == 0
        assert "bad-bot" in caplog.text

    def test_load_mixed_entries_normalizes_and_skips(self, tmp_path, monkeypatch, caplog):
        """Valid uppercase, lowercase-fixable, and invalid symbol are processed correctly:
        2 bots loaded (both valid after normalization), 1 skipped."""
        bots_file = tmp_path / "bots.json"
        entries = [
            _make_bot_entry("bot-ok", "SPY"),
            _make_bot_entry("bot-lower", "qqq"),
            _make_bot_entry("bot-bad", "AA@PL"),
        ]
        self._write_bots_json(bots_file, entries)
        monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(bots_file))

        mgr = BotManager()
        with caplog.at_level(_logging.WARNING, logger="bot_manager"):
            mgr.load()

        assert len(mgr.bots) == 2
        assert "bot-ok" in mgr.bots
        assert "bot-lower" in mgr.bots
        assert "bot-bad" not in mgr.bots

        config_lower, _ = mgr.bots["bot-lower"]
        assert config_lower.symbol == "QQQ"

        # Warning must mention the skipped bot ID
        assert "bot-bad" in caplog.text

    def test_load_skips_bot_with_none_symbol(self, tmp_path, monkeypatch, caplog):
        """A bots.json entry with symbol: null is skipped; remaining bots still load."""
        bots_file = tmp_path / "bots.json"
        null_entry = _make_bot_entry("null-sym-bot", "SPY")
        null_entry["config"]["symbol"] = None
        good_entry = _make_bot_entry("good-bot", "AAPL")
        self._write_bots_json(bots_file, [null_entry, good_entry])
        monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(bots_file))

        mgr = BotManager()
        with caplog.at_level(_logging.WARNING, logger="bot_manager"):
            mgr.load()

        assert "null-sym-bot" not in mgr.bots
        assert "good-bot" in mgr.bots
        assert "null-sym-bot" in caplog.text

    def test_load_skips_bot_with_missing_symbol_key(self, tmp_path, monkeypatch, caplog):
        """A bots.json entry with no 'symbol' key at all is skipped with a warning."""
        bots_file = tmp_path / "bots.json"
        missing_entry = _make_bot_entry("missing-sym-bot", "SPY")
        del missing_entry["config"]["symbol"]
        good_entry = _make_bot_entry("good-bot2", "MSFT")
        self._write_bots_json(bots_file, [missing_entry, good_entry])
        monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(bots_file))

        mgr = BotManager()
        with caplog.at_level(_logging.WARNING, logger="bot_manager"):
            mgr.load()

        assert "missing-sym-bot" not in mgr.bots
        assert "good-bot2" in mgr.bots
