"""HTTP-level tests for POST /api/nodebuilder/backtest (F435 item 0.A).

Contract the editor builds against:
  - every graph error is a 400 with {"detail": <message>, "node_id": <id or null>,
    "code": <diagnostic code>, "diagnostics": [<plan 4.2 Diagnostic>, ...]}
  - other bad fields keep FastAPI's 422
  - summary carries open_position ({direction, entry_price, unrealized_pct} or
    null) and exit_connected (bool)

Data comes from a synthetic frame; shared._fetch is patched, so no network.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import shared
from nodebuilder.run import _open_position
from routes.nodebuilder import router

URL = "/api/nodebuilder/backtest"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _df(n: int = 120) -> pd.DataFrame:
    t = np.arange(n)
    close = 100.0 + 10.0 * np.sin(t / 6.0)
    idx = pd.date_range("2023-01-02", periods=n, freq="B", tz="America/New_York", name="Date")
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1_000_000},
        index=idx,
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(shared, "_fetch", lambda *a, **k: _df())
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _n(node_id: str, node_type: str, **params) -> dict:
    return {"id": node_id, "type": node_type, "params": params}


def _w(wire_id: str, src: str, dst: str, attr: str | None = None) -> dict:
    return {"id": wire_id, "from": src, "to": dst, "attr": attr}


def _body(nodes: list[dict], wires: list[dict], **extra) -> dict:
    graph = {"_version": 1, "nodes": {n["id"]: n for n in nodes}, "wires": wires}
    return {"graph": graph, "ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01", **extra}


def _rsi_entry(exit_wired: bool = True) -> dict:
    """Ticker -> RSI -> below 45 -> Entry, and optionally RSI above 55 -> Exit."""
    nodes = [
        _n("/ticker", "ticker"),
        _n("/rsi", "rsi", period=14, type="sma"),
        _n("/below", "below", threshold=45.0),
        _n("/entry", "entry"),
        _n("/exit", "exit"),
    ]
    wires = [
        _w("w1", "/ticker", "/rsi", "@close"),
        _w("w2", "/rsi", "/below", "@rsi"),
        _w("w3", "/below", "/entry", "@bool"),
    ]
    if exit_wired:
        nodes.append(_n("/above", "above", threshold=55.0))
        wires += [_w("w4", "/rsi", "/above", "@rsi"), _w("w5", "/above", "/exit", "@bool")]
    return _body(nodes, wires)


def _assert_graph_error(resp, node_id):
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert set(body) == {"detail", "node_id", "code", "diagnostics"}
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["node_id"] == node_id
    assert isinstance(body["code"], str) and body["code"]
    assert isinstance(body["diagnostics"], list) and body["diagnostics"]
    assert any(
        d["severity"] == "error" and d["code"] == body["code"] and d["node_id"] == node_id
        for d in body["diagnostics"]
    )


# ---------------------------------------------------------------------------
# 400 with {detail, node_id}
# ---------------------------------------------------------------------------

def test_missing_entry_is_400_without_node(client):
    body = _body(
        [_n("/ticker", "ticker"), _n("/rsi", "rsi"), _n("/below", "below", threshold=30.0)],
        [_w("w1", "/ticker", "/rsi"), _w("w2", "/rsi", "/below")],
    )
    resp = client.post(URL, json=body)
    _assert_graph_error(resp, None)
    assert "Entry" in resp.json()["detail"]


def test_unwired_entry_names_the_entry_node(client):
    body = _body([_n("/ticker", "ticker"), _n("/entry", "entry")], [])
    _assert_graph_error(client.post(URL, json=body), "/entry")


def test_regime_node_is_400_with_node_id(client):
    body = _rsi_entry()
    body["graph"]["nodes"]["/regime/ticker"] = _n("/regime/ticker", "ticker")
    _assert_graph_error(client.post(URL, json=body), "/regime/ticker")


def test_crossover_of_derived_signal_is_400(client):
    body = _body(
        [
            _n("/ticker", "ticker"), _n("/rsi", "rsi"),
            _n("/b1", "below", threshold=30.0), _n("/b2", "below", threshold=40.0),
            _n("/x", "crosses_above"), _n("/entry", "entry"),
        ],
        [
            _w("w1", "/ticker", "/rsi"), _w("w2", "/rsi", "/b1"), _w("w3", "/rsi", "/b2"),
            _w("w4", "/b1", "/x"), _w("w5", "/b2", "/x"), _w("w6", "/x", "/entry"),
        ],
    )
    _assert_graph_error(client.post(URL, json=body), "/x")


def test_price_into_entry_names_the_source(client):
    body = _body([_n("/ticker", "ticker"), _n("/entry", "entry")], [_w("w1", "/ticker", "/entry")])
    _assert_graph_error(client.post(URL, json=body), "/ticker")


def test_cycle_is_400_not_500(client):
    body = _body(
        [_n("/a", "and"), _n("/b", "or")],
        [_w("w1", "/a", "/b"), _w("w2", "/b", "/a")],
    )
    _assert_graph_error(client.post(URL, json=body), "/a")


def test_dangling_wire_is_400_not_500(client):
    body = _body([_n("/a", "and")], [_w("w1", "/a", "/missing")])
    _assert_graph_error(client.post(URL, json=body), "/a")


def test_family_cap_is_400_with_node_id(client):
    nodes = [_n("/ticker", "ticker"), _n("/entry", "entry")]
    wires = []
    for p in range(2, 23):  # 21 distinct RSI specs, one over the cap of 20
        nodes.append(_n(f"/rsi{p}", "rsi", period=p, type="sma"))
        wires.append(_w(f"w{p}", "/ticker", f"/rsi{p}"))
    nodes.append(_n("/below", "below", threshold=30.0))
    wires += [_w("wb", "/rsi2", "/below"), _w("we", "/below", "/entry")]
    resp = client.post(URL, json=_body(nodes, wires))
    assert resp.status_code == 400, resp.text
    assert resp.json()["node_id"] is not None
    assert resp.json()["node_id"].startswith("/rsi")


def test_unsupported_condition_extra_is_400(client):
    body = _rsi_entry()
    body["graph"]["nodes"]["/below"]["params"]["condition_extra"] = "atr_pct"
    _assert_graph_error(client.post(URL, json=body), "/below")


def test_unsupported_node_type_is_400_with_node_id(client):
    """A node compile cannot run (here a slope condition feeding Exit) is
    refused, not skipped into an Exit that never fires."""
    body = _rsi_entry()
    body["graph"]["nodes"]["/above"]["type"] = "rising"
    _assert_graph_error(client.post(URL, json=body), "/above")


def test_wired_size_terminal_is_400(client):
    body = _rsi_entry()
    body["graph"]["nodes"]["/size"] = _n("/size", "size")
    body["graph"]["wires"].append(_w("ws", "/below", "/size", "@bool"))
    _assert_graph_error(client.post(URL, json=body), "/size")


def test_bad_trailing_stop_params_are_400_with_node_id(client):
    body = _rsi_entry()
    body["graph"]["nodes"]["/trail"] = _n("/trail", "trailing_stop", type="chandelier", value=5.0)
    _assert_graph_error(client.post(URL, json=body), "/trail")


def test_trailing_stop_node_runs(client):
    body = _rsi_entry()
    body["graph"]["nodes"]["/trail"] = _n(
        "/trail", "trailing_stop", type="pct", value=3.0, source="high",
        activate_on_profit="false", activate_pct=0.0,
    )
    resp = client.post(URL, json=body)
    assert resp.status_code == 200, resp.text


def test_sub_output_from_wrong_node_type_is_400(client):
    body = _rsi_entry()
    body["graph"]["wires"][1]["attr"] = "@macd_signal"  # RSI has no MACD signal
    _assert_graph_error(client.post(URL, json=body), "/below")


def test_invalid_source_is_400_with_null_node(client):
    body = _rsi_entry()
    body["source"] = "not-a-provider"
    _assert_graph_error(client.post(URL, json=body), None)


def test_bad_field_keeps_422(client):
    body = _rsi_entry()
    body["initial_capital"] = "lots"
    resp = client.post(URL, json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"][0] == "body"


# ---------------------------------------------------------------------------
# summary.open_position and summary.exit_connected
# ---------------------------------------------------------------------------

def test_success_summary_has_open_position_and_exit_connected(client):
    resp = client.post(URL, json=_rsi_entry())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == {"summary", "trades", "equity_curve", "baseline_curve"}
    assert data["summary"]["exit_connected"] is True
    assert "open_position" in data["summary"]
    assert data["summary"]["num_trades"] > 0


def test_exit_unwired_reports_open_position(client):
    resp = client.post(URL, json=_rsi_entry(exit_wired=False))
    assert resp.status_code == 200, resp.text
    s = resp.json()["summary"]
    assert s["exit_connected"] is False
    # Entry fires and nothing ever exits: zero closed trades, one open position.
    assert s["num_trades"] == 0
    op = s["open_position"]
    assert op is not None
    assert set(op) == {"direction", "entry_price", "unrealized_pct"}
    assert op["direction"] == "long"
    last_close = float(_df()["Close"].iloc[-1])
    expected = (last_close - op["entry_price"]) / op["entry_price"] * 100
    assert op["unrealized_pct"] == pytest.approx(expected, abs=0.01)


def test_open_position_helper():
    assert _open_position([], 100.0) is None
    closed = [{"type": "buy", "price": 10.0, "direction": "long"}, {"type": "sell", "price": 11.0}]
    assert _open_position(closed, 100.0) is None
    long_open = closed + [{"type": "buy", "price": 100.0, "direction": "long"}]
    assert _open_position(long_open, 110.0) == {
        "direction": "long", "entry_price": 100.0, "unrealized_pct": 10.0,
    }
    short_open = [{"type": "short", "price": 100.0, "direction": "short"}]
    assert _open_position(short_open, 90.0) == {
        "direction": "short", "entry_price": 100.0, "unrealized_pct": 10.0,
    }
