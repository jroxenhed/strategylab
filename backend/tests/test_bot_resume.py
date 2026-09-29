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


async def test_shutdown_keeps_running_status_for_next_boot(tmp_path, monkeypatch):
    """The runner's `finally` (status="stopped" + save) must not win over shutdown."""
    import asyncio
    mgr, _, f = _manager(tmp_path, monkeypatch, [_entry("live", "running"), _entry("off", "stopped")])
    state = mgr.bots["live"][1]

    async def fake_run():
        state.status = "running"
        try:
            await asyncio.sleep(3600)
        finally:
            state.status = "stopped"
            await asyncio.to_thread(mgr.save)

    mgr.tasks["live"] = asyncio.create_task(fake_run())
    await asyncio.sleep(0)
    await mgr.shutdown()
    # the event loop still runs cancelled runners' `finally` after the lifespan exits
    await asyncio.gather(*mgr.tasks.values(), return_exceptions=True)

    saved ={b["config"]["bot_id"]: b["state"] for b in json.loads(f.read_text())["bots"]}
    assert saved["live"]["status"] == "running" and saved["off"]["status"] == "stopped"
    reloaded, started, _ = _manager(tmp_path, monkeypatch, json.loads(f.read_text())["bots"])
    reloaded.resume_was_running()
    assert started == ["live"]


def test_overlapping_saves_keep_newest_state(tmp_path, monkeypatch):
    """Runners save via asyncio.to_thread; an older snapshot must not land last."""
    import threading
    import time
    mgr, _, f = _manager(tmp_path, monkeypatch, [_entry("a", "stopped")])
    real_write = _bot_manager_mod.atomic_write_text
    first = threading.Event()

    def slow_first(path, content, **kw):
        if not first.is_set():
            first.set()
            time.sleep(0.2)  # the older snapshot is slow to land
        real_write(path, content, **kw)

    monkeypatch.setattr(_bot_manager_mod, "atomic_write_text", slow_first)
    older = threading.Thread(target=mgr.save)
    older.start()
    first.wait(1)
    mgr.bots["a"][1].status = "running"
    mgr.save()
    older.join()
    assert json.loads(f.read_text())["bots"][0]["state"]["status"] == "running"


def test_opt_out_env(tmp_path, monkeypatch):
    mgr, started, _ = _manager(tmp_path, monkeypatch, [_entry("a", "running")])
    monkeypatch.setenv("BOTS_AUTORESUME", "0")
    r = mgr.resume_was_running()
    assert started == [] and r == {"resumed": [], "skipped": [], "failed": []}
    assert mgr.bots["a"][1].was_running is True  # untouched; a later manual start is still possible
