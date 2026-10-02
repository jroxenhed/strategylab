"""F435 W2 MD-01 / LT-3: rollback safety for bots.json.

The first Wave 2 boot rewrites every graph bot's graph as version 3, which
Wave 1 code refuses.  load() therefore keeps a one-time copy,
bots.json.pre-w2, of the file as it was.  And a row that fails to load at
boot sends one alert naming the bot ids (it is not listed, not resumed and
not watched, so it must not be silent).

Temporary STRATEGYLAB_DATA_DIR; notify_error is replaced by a recorder.  No
bot is started (load() leaves every bot stopped) and no broker is touched.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

import bot_manager as _bot_manager_mod
import notifications
from bot_manager import BotManager
from nodebuilder.migrate import CURRENT_GRAPH_VERSION

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "fixtures", "v2_autorender_corpus.json")) as _fh:
    _CORPUS = json.load(_fh)["cases"]


def _v2_graph() -> dict:
    case = next(c for c in _CORPUS if c.get("compiles") and c["graph"].get("_version") == 2)
    return case["graph"]


def _row(bot_id: str, graph: dict | None = None, symbol: str = "AAPL") -> dict:
    cfg = {
        "bot_id": bot_id, "strategy_name": "t", "symbol": symbol, "interval": "1d",
        "buy_rules": [], "sell_rules": [], "allocated_capital": 100.0,
    }
    if graph is not None:
        cfg.update(kind="graph", graph=graph)
    return {"config": cfg, "state": {"status": "running"}}


@pytest.fixture
def bots_file(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    path = tmp_path / "bots.json"
    monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(path))
    return path


@pytest.fixture
def alerts(monkeypatch):
    sent: list[dict] = []

    async def _record(symbol, error_msg, bot_id):
        sent.append({"symbol": symbol, "error_msg": error_msg, "bot_id": bot_id})

    monkeypatch.setattr(notifications, "notify_error", _record)
    return sent


# ---------------------------------------------------------------------------
# MD-01: bots.json.pre-w2
# ---------------------------------------------------------------------------


def test_first_w2_boot_keeps_a_pre_w2_copy(bots_file):
    original = json.dumps({"bot_fund": 500.0, "bots": [_row("g-1", _v2_graph())]}, indent=2)
    bots_file.write_text(original)

    mgr = BotManager()
    mgr.load()

    copy_path = bots_file.parent / "bots.json.pre-w2"
    assert copy_path.read_text() == original, "the copy is the file Wave 1 wrote, byte for byte"
    saved = json.loads(bots_file.read_text())
    assert saved["bots"][0]["config"]["graph"]["version"] == CURRENT_GRAPH_VERSION
    assert mgr.bots["g-1"][1].status == "stopped"


def test_pre_w2_copy_is_never_overwritten(bots_file):
    copy_path = bots_file.parent / "bots.json.pre-w2"
    copy_path.write_text("the first copy")
    bots_file.write_text(json.dumps({"bot_fund": 0.0, "bots": [_row("g-1", _v2_graph())]}))

    BotManager().load()
    assert copy_path.read_text() == "the first copy"


def test_no_copy_when_every_graph_is_already_current(bots_file):
    bots_file.write_text(json.dumps({"bot_fund": 0.0, "bots": [_row("g-1", _v2_graph())]}))
    mgr = BotManager()
    mgr.load()  # first boot: copy written, file now v3
    copy_path = bots_file.parent / "bots.json.pre-w2"
    copy_path.unlink()

    BotManager().load()  # second boot: nothing below v3 on disk
    assert not copy_path.exists()


def test_no_copy_for_a_file_with_only_rule_bots(bots_file):
    bots_file.write_text(json.dumps({"bot_fund": 0.0, "bots": [_row("r-1")]}))
    BotManager().load()
    assert not (bots_file.parent / "bots.json.pre-w2").exists()


# ---------------------------------------------------------------------------
# LT-3: one alert for rows that did not load
# ---------------------------------------------------------------------------


def _with_bad_rows(bots_file) -> None:
    newer = {**_v2_graph(), "_version": CURRENT_GRAPH_VERSION + 1}
    bots_file.write_text(json.dumps({"bot_fund": 0.0, "bots": [
        _row("good-1", _v2_graph()),
        _row("bad-1", newer),
        _row("bad-2", symbol="not a symbol!!"),
    ]}))


def test_unloaded_rows_send_one_alert_when_a_loop_runs(bots_file, alerts):
    _with_bad_rows(bots_file)

    async def boot():
        mgr = BotManager()
        mgr.load()
        await asyncio.sleep(0)  # let the fire-and-forget task run
        await asyncio.sleep(0)
        return mgr

    mgr = asyncio.run(boot())
    assert set(mgr.bots) == {"good-1"}
    assert len(alerts) == 1
    assert "bad-1" in alerts[0]["error_msg"] and "bad-2" in alerts[0]["error_msg"]
    # The rows are kept on disk unchanged.
    ids = [r["config"]["bot_id"] for r in json.loads(bots_file.read_text())["bots"]]
    assert ids == ["good-1", "bad-1", "bad-2"]


def test_unloaded_rows_without_a_loop_only_log(bots_file, alerts, caplog):
    _with_bad_rows(bots_file)
    with caplog.at_level("ERROR", logger=_bot_manager_mod.logger.name):
        BotManager().load()
    assert alerts == []
    assert any("did not load" in r.getMessage() and "bad-1" in r.getMessage()
               for r in caplog.records)


def test_no_alert_when_every_row_loads(bots_file, alerts):
    bots_file.write_text(json.dumps({"bot_fund": 0.0, "bots": [_row("good-1", _v2_graph())]}))

    async def boot():
        BotManager().load()
        await asyncio.sleep(0)

    asyncio.run(boot())
    assert alerts == []
