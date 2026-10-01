"""POST /api/nodebuilder/inspect (F435 W4 item 4.A, plan D6 + Wave 4 contracts).

The wire inspector reads a cook from the cook cache; a miss cooks the graph
over the window.  Data comes from a synthetic frame (shared._fetch is
patched), so no network.
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
from nodebuilder.cook_cache import COOK_CACHE
from routes.nodebuilder import router

BACKTEST = "/api/nodebuilder/backtest"
INSPECT = "/api/nodebuilder/inspect"
N_BARS = 120


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _daily(n: int = N_BARS) -> pd.DataFrame:
    t = np.arange(n)
    close = 100.0 + 10.0 * np.sin(t / 6.0)
    idx = pd.date_range("2023-01-02", periods=n, freq="B", tz="America/New_York", name="Date")
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1_000_000},
        index=idx,
    )


def _intraday(n: int = 60) -> pd.DataFrame:
    t = np.arange(n)
    close = 50.0 + np.cos(t / 4.0)
    idx = pd.date_range("2024-03-04 14:30", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame(
        {"Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close, "Volume": 500.0},
        index=idx,
    )


def _n(node_id: str, node_type: str, **params) -> dict:
    return {"id": node_id, "type": node_type, "params": params}


def _w(wire_id: str, src: str, dst: str) -> dict:
    return {"id": wire_id, "from": src, "to": dst}


def _graph(bypass_sma: bool = False) -> dict:
    """Ticker -> SMA(5) -> RSI(14, reads @close) -> below 45 -> Entry,
    and RSI -> above 55 -> Exit.  The SMA can be bypassed."""
    sma = _n("/sma", "sma", period=5)
    sma["bypass"] = bypass_sma
    nodes = [
        _n("/ticker", "ticker"), sma,
        _n("/rsi", "rsi", period=14, type="sma", source="@close"),
        _n("/below", "below", threshold=45.0), _n("/entry", "entry"),
        _n("/above", "above", threshold=55.0), _n("/exit", "exit"),
    ]
    wires = [
        _w("w1", "/ticker", "/sma"), _w("w2", "/sma", "/rsi"), _w("w3", "/rsi", "/below"),
        _w("w4", "/below", "/entry"), _w("w5", "/rsi", "/above"), _w("w6", "/above", "/exit"),
    ]
    return {"nodes": {n["id"]: n for n in nodes}, "wires": wires}


WINDOW = {"ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01", "interval": "1d", "source": "yahoo"}


def _backtest(client, graph: dict | None = None, **window) -> dict:
    win = {**WINDOW, **window}
    resp = client.post(BACKTEST, json={"graph": graph or _graph(), **win})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def frame():
    """The frame _fetch returns; a test may swap it."""
    return {"df": _daily()}


@pytest.fixture
def client(monkeypatch, frame):
    monkeypatch.setattr(shared, "_fetch", lambda *a, **k: frame["df"])
    COOK_CACHE.clear()
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app, raise_server_exceptions=False)
    COOK_CACHE.clear()


def _names(body: dict) -> list[str]:
    return [c["name"] for c in body["columns"]]


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_backtest_response_carries_a_cook_id(client):
    body = _backtest(client)
    assert isinstance(body["cook_id"], str) and body["cook_id"].startswith("ck_")
    assert body["cook_id"] in COOK_CACHE


def test_node_target_shape(client):
    cook_id = _backtest(client)["cook_id"]
    resp = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cook_id"] == cook_id
    assert body["cache"] == "hit"
    assert body["stream_schema"] == 1
    assert body["prims"] == []
    assert "read_by_consumer" not in body          # wire targets only
    names = _names(body)
    assert names[0] == "@time" and body["columns"][0]["dtype"] == "time"
    assert names.count("@time") == 1               # the Ticker's own @time is not repeated
    assert "@rsi" in names and "@sma" in names and "@close" in names
    rsi_col = next(c for c in body["columns"] if c["name"] == "@rsi")
    assert rsi_col == {"name": "@rsi", "dtype": "float", "written_by": "/rsi"}
    assert body["total"] == N_BARS and body["offset"] == 0
    assert body["total_unfiltered"] == N_BARS     # no filter: same as total
    assert len(body["rows"]) == N_BARS == len(body["time"])
    assert all(len(r) == len(names) - 1 for r in body["rows"])   # time excluded
    assert body["time"][0] == "2023-01-02"                     # daily: "YYYY-MM-DD"
    # RSI warmup is NaN: sent as null.
    i = names.index("@rsi") - 1
    assert body["rows"][0][i] is None
    assert any(isinstance(r[i], float) for r in body["rows"])


def test_stats_histogram_and_bool_counts(client):
    cook_id = _backtest(client)["cook_id"]
    body = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/below"}}).json()
    rsi = body["stats"]["@rsi"]
    assert len(rsi["hist"]["edges"]) == 21 and len(rsi["hist"]["counts"]) == 20
    assert sum(rsi["hist"]["counts"]) + rsi["nan_count"] == N_BARS
    assert rsi["min"] == pytest.approx(rsi["hist"]["edges"][0])
    assert rsi["max"] == pytest.approx(rsi["hist"]["edges"][-1])
    assert rsi["nan_count"] > 0
    below = body["stats"]["@below"]
    assert set(below) == {"true_count"}
    i = _names(body).index("@below") - 1
    assert below["true_count"] == sum(1 for r in body["rows"] if r[i] is True)
    assert body["columns"][_names(body).index("@below")]["dtype"] == "bool"


def test_attrs_limits_the_columns(client):
    cook_id = _backtest(client)["cook_id"]
    body = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/below"}, "attrs": ["@below", "@rsi", "@nope"],
    }).json()
    assert _names(body) == ["@time", "@below", "@rsi"]
    assert set(body["stats"]) == {"@below", "@rsi"}


def test_intraday_time_is_unix_seconds(client, frame):
    frame["df"] = _intraday()
    cook_id = _backtest(client, interval="5m", start="2024-03-04", end="2024-03-05")["cook_id"]
    body = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/ticker"}, "limit": 2}).json()
    assert body["time"] == [int(pd.Timestamp("2024-03-04 14:30", tz="UTC").timestamp()),
                            int(pd.Timestamp("2024-03-04 14:35", tz="UTC").timestamp())]


# ---------------------------------------------------------------------------
# Wires and bypass
# ---------------------------------------------------------------------------

def test_wire_target_is_the_source_stream_with_the_consumer_reads(client):
    cook_id = _backtest(client)["cook_id"]
    wire = client.post(INSPECT, json={"cook_id": cook_id, "target": {"wire_id": "w3"}}).json()
    node = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}}).json()
    assert wire["columns"] == node["columns"] and wire["rows"] == node["rows"]
    assert wire["read_by_consumer"] == ["@rsi"]            # /below reads @rsi
    # The RSI reads @close (its source param) from the stream on w2.
    w2 = client.post(INSPECT, json={"cook_id": cook_id, "target": {"wire_id": "w2"}}).json()
    assert w2["read_by_consumer"] == ["@close"]


def test_bypassed_node_returns_its_input_stream(client):
    cook_id = _backtest(client, graph=_graph(bypass_sma=True))["cook_id"]
    sma = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/sma"}}).json()
    ticker = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/ticker"}}).json()
    assert "@sma" not in _names(sma)
    assert sma["columns"] == ticker["columns"]
    assert sma["rows"] == ticker["rows"]


def test_unknown_target_is_404(client):
    cook_id = _backtest(client)["cook_id"]
    for target in ({"node_id": "/nope"}, {"wire_id": "w99"}):
        resp = client.post(INSPECT, json={"cook_id": cook_id, "target": target})
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "target_not_found"


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------

def test_paging_offset_and_limit(client):
    cook_id = _backtest(client)["cook_id"]
    full = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}}).json()
    page = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "offset": 30, "limit": 25,
    }).json()
    assert page["total"] == N_BARS and page["offset"] == 30
    assert page["rows"] == full["rows"][30:55]
    assert page["time"] == full["time"][30:55]
    # Past the end: an empty page, offset clamped to the total.
    tail = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "offset": 500, "limit": 10,
    }).json()
    assert tail["rows"] == [] and tail["time"] == [] and tail["offset"] == N_BARS


def test_limit_above_2000_is_rejected(client):
    cook_id = _backtest(client)["cook_id"]
    resp = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}, "limit": 2001})
    assert resp.status_code == 422


def test_around_time_centres_the_page(client):
    cook_id = _backtest(client)["cook_id"]
    full = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}}).json()
    at = full["time"].index("2023-03-01")
    page = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "limit": 10, "around_time": "2023-03-01",
    }).json()
    assert page["offset"] == at - 5
    assert page["time"][5] == "2023-03-01"
    # Near the start the page cannot centre: it starts at 0.
    first = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "limit": 10, "around_time": "2023-01-03",
    }).json()
    assert first["offset"] == 0 and "2023-01-03" in first["time"]


def test_around_time_unix_seconds_on_intraday(client, frame):
    frame["df"] = _intraday()
    cook_id = _backtest(client, interval="5m", start="2024-03-04", end="2024-03-05")["cook_id"]
    target = int(pd.Timestamp("2024-03-04 16:30", tz="UTC").timestamp())
    page = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/ticker"}, "limit": 6, "around_time": target,
    }).json()
    assert page["time"][3] == target


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def test_filter_is_true_keeps_only_matching_rows(client):
    cook_id = _backtest(client)["cook_id"]
    body = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/below"},
        "filter": {"attr": "@below", "op": "is_true", "value": None},
    }).json()
    i = _names(body).index("@below") - 1
    assert body["total"] == body["stats"]["@below"]["true_count"] > 0
    assert all(r[i] is True for r in body["rows"])


def test_filtered_page_reports_the_unfiltered_total(client):
    """The Data Sheet prints "X of Y rows": total is after the filter and
    total_unfiltered is every bar of the cook, on every filtered page."""
    cook_id = _backtest(client)["cook_id"]
    body = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/below"}, "offset": 1, "limit": 3,
        "filter": {"attr": "@below", "op": "is_true", "value": None},
    }).json()
    assert 0 < body["total"] < N_BARS
    assert body["total_unfiltered"] == N_BARS


def test_filter_gt_lt_and_not_nan(client):
    cook_id = _backtest(client)["cook_id"]

    def run(op, value=None):
        return client.post(INSPECT, json={
            "cook_id": cook_id, "target": {"node_id": "/rsi"}, "attrs": ["@rsi"],
            "filter": {"attr": "@rsi", "op": op, "value": value},
        }).json()

    gt, lt, ok = run("gt", 60.0), run("lt", 60.0), run("not_nan")
    assert all(r[0] > 60 for r in gt["rows"]) and gt["total"] > 0
    assert all(r[0] < 60 for r in lt["rows"]) and lt["total"] > 0
    nan_count = ok["stats"]["@rsi"]["nan_count"]
    assert ok["total"] == N_BARS - nan_count
    assert gt["total"] + lt["total"] <= ok["total"]


def test_filter_with_paging_and_around_time(client):
    cook_id = _backtest(client)["cook_id"]
    flt = {"attr": "@rsi", "op": "not_nan", "value": None}
    full = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "filter": flt,
    }).json()
    page = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "filter": flt, "offset": 10, "limit": 5,
    }).json()
    assert page["total"] == full["total"]
    assert page["time"] == full["time"][10:15]
    centred = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "filter": flt, "limit": 4,
        "around_time": full["time"][40],
    }).json()
    assert centred["time"][2] == full["time"][40]


def test_filter_on_an_unknown_attr_is_422(client):
    cook_id = _backtest(client)["cook_id"]
    resp = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"},
        "filter": {"attr": "@nope", "op": "is_true", "value": None},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "attr_unknown"


def test_gt_without_a_value_is_422(client):
    cook_id = _backtest(client)["cook_id"]
    resp = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"},
        "filter": {"attr": "@rsi", "op": "gt", "value": None},
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Cache misses and expiry
# ---------------------------------------------------------------------------

def test_unknown_cook_without_a_graph_is_410(client):
    resp = client.post(INSPECT, json={"cook_id": "ck_gone", "target": {"node_id": "/rsi"}})
    assert resp.status_code == 410
    assert resp.json() == {"detail": {"code": "cook_expired"}}


def test_expired_cook_with_a_graph_cooks_again(client):
    cook_id = _backtest(client)["cook_id"]
    COOK_CACHE.clear()
    gone = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}})
    assert gone.status_code == 410
    resp = client.post(INSPECT, json={
        "cook_id": cook_id, "graph": _graph(), "window": WINDOW, "target": {"node_id": "/rsi"},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cache"] == "miss"
    assert body["cook_id"] == cook_id      # same graph, same frame: the same id
    again = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}}).json()
    assert again["cache"] == "hit"


def test_graph_without_a_window_or_live_cook_is_422(client):
    resp = client.post(INSPECT, json={"graph": _graph(), "target": {"node_id": "/rsi"}})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "window_required"


def test_a_changed_graph_with_an_old_cook_id_cooks_the_new_graph(client):
    cook_id = _backtest(client)["cook_id"]
    g = _graph()
    g["nodes"]["/rsi"]["params"]["period"] = 7
    body = client.post(INSPECT, json={
        "cook_id": cook_id, "graph": g, "target": {"node_id": "/rsi"},   # window from the old cook
    }).json()
    assert body["cache"] == "miss" and body["cook_id"] != cook_id


def test_graph_error_on_a_miss_is_the_plan_400(client):
    g = _graph()
    del g["nodes"]["/entry"]
    g["wires"] = [w for w in g["wires"] if w["to"] != "/entry"]
    resp = client.post(INSPECT, json={"graph": g, "window": WINDOW, "target": {"node_id": "/rsi"}})
    assert resp.status_code == 400
    body = resp.json()
    assert set(body) == {"detail", "node_id", "code", "diagnostics"}
