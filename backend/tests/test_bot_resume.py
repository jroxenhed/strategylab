"""F430: BotManager.resume_was_running() — auto-resume bots that were running when
the server last went away, but never bots the user explicitly stopped, paused, or
that carry an error."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json

import bot_manager as _bot_manager_mod
from bot_manager import BotManager


def _entry(bot_id: str, status: str, **state) -> dict:
    return {
        "config": {
            "bot_id": bot_id, "strategy_name": "Test", "symbol": "AAPL", "interval": "5m",
            "buy_rules": [], "sell_rules": [], "allocated_capital": 100.0,
        },
        "state": {"status": status, **state},
    }


def _manager(tmp_path, monkeypatch, entries):
    f = tmp_path / "bots.json"
    f.write_text(json.dumps({"bot_fund": 1000.0, "bots": entries}))
    monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(f))
    mgr = BotManager()
    mgr.load()
    started: list[str] = []
    monkeypatch.setattr(mgr, "start_bot", lambda bot_id: started.append(bot_id))
    return mgr, started, f


def test_load_marks_was_running_and_forces_stopped(tmp_path, monkeypatch):
    mgr, _, _ = _manager(tmp_path, monkeypatch, [_entry("a", "running"), _entry("b", "stopped")])
    assert mgr.bots["a"][1].was_running is True and mgr.bots["a"][1].status == "stopped"
    assert mgr.bots["b"][1].was_running is False


def test_resumes_only_previously_running_bots(tmp_path, monkeypatch):
    mgr, started, _ = _manager(tmp_path, monkeypatch, [
        _entry("run1", "running"), _entry("run2", "running"), _entry("off", "stopped"),
    ])
    r = mgr.resume_was_running()
    assert sorted(started) == ["run1", "run2"]
    assert sorted(r["resumed"]) == ["run1", "run2"] and r["skipped"] == [] and r["failed"] == []


def test_skips_paused_and_errored_bots(tmp_path, monkeypatch):
    mgr, started, f = _manager(tmp_path, monkeypatch, [
        _entry("paused", "running", pause_reason="Auto-paused: drawdown"),
        _entry("broken", "running", error_message="boom"),
        _entry("ok", "running"),
    ])
    r = mgr.resume_was_running()
    assert started == ["ok"]
    assert sorted(r["skipped"]) == ["broken", "paused"]
    # skipped bots lose the flag so a later restart does not retry them
    saved = {b["config"]["bot_id"]: b["state"] for b in json.loads(f.read_text())["bots"]}
    assert saved["paused"]["was_running"] is False and saved["broken"]["was_running"] is False


def test_start_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    mgr, _, _ = _manager(tmp_path, monkeypatch, [_entry("x", "running"), _entry("y", "running")])
    def boom(bot_id):
        if bot_id == "x":
            raise ValueError("Bot z is already running long on AAPL")
    monkeypatch.setattr(mgr, "start_bot", boom)
    r = mgr.resume_was_running()
    assert r["resumed"] == ["y"]
    assert r["failed"] == [{"bot_id": "x", "error": "Bot z is already running long on AAPL"}]
    assert mgr.bots["x"][1].was_running is False


def test_opt_out_env(tmp_path, monkeypatch):
    mgr, started, _ = _manager(tmp_path, monkeypatch, [_entry("a", "running")])
    monkeypatch.setenv("BOTS_AUTORESUME", "0")
    r = mgr.resume_was_running()
    assert started == [] and r == {"resumed": [], "skipped": [], "failed": []}
    assert mgr.bots["a"][1].was_running is True  # untouched; a later manual start is still possible
