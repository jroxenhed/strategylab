"""POST /api/nodebuilder/preview (F435 W4 item 4.A, plan D6, surfaces S26/S27).

Sparkline data: each node's primary write, decimated to `points` values.
Data comes from a synthetic frame (shared._fetch is patched).
"""
from __future__ import annotations

import numpy as np
import pytest

from nodebuilder.cook_cache import COOK_CACHE, decimate_bool, decimate_line
from tests.nodebuilder.test_inspect import (  # noqa: F401  (client, frame are fixtures)
    N_BARS,
    WINDOW,
    _backtest,
    _graph,
    _intraday,
    client,
    frame,
)

PREVIEW = "/api/nodebuilder/preview"
INSPECT = "/api/nodebuilder/inspect"


def test_preview_returns_96_points_for_every_node(client):
    cook_id = _backtest(client)["cook_id"]
    resp = client.post(PREVIEW, json={"cook_id": cook_id, "graph": None, "window": None,
                                      "node_ids": None, "points": 96})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cook_id"] == cook_id
    nodes = body["nodes"]
    # Terminals write nothing: no sparkline.
    assert set(nodes) == {"/ticker", "/sma", "/rsi", "/below", "/above"}
    for node in nodes.values():
        assert len(node["values"]) == 96
    assert nodes["/ticker"]["attr"] == "@close"
    assert nodes["/rsi"]["attr"] == "@rsi" and nodes["/rsi"]["kind"] == "line"


def test_points_default_is_96(client):
    cook_id = _backtest(client)["cook_id"]
    body = client.post(PREVIEW, json={"cook_id": cook_id}).json()
    assert all(len(n["values"]) == 96 for n in body["nodes"].values())


def test_bool_node_has_true_pct_and_shares(client):
    cook_id = _backtest(client)["cook_id"]
    node = client.post(PREVIEW, json={"cook_id": cook_id, "node_ids": ["/below"]}).json()["nodes"]["/below"]
    assert node["kind"] == "bool" and node["attr"] == "@below"
    inspect = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/below"}}).json()
    true_count = inspect["stats"]["@below"]["true_count"]
    assert node["true_pct"] == pytest.approx(100.0 * true_count / N_BARS)
    assert all(0.0 <= v <= 1.0 for v in node["values"])
    assert "min" not in node or node["min"] is None


def test_line_node_stats_match_inspect(client):
    cook_id = _backtest(client)["cook_id"]
    node = client.post(PREVIEW, json={"cook_id": cook_id, "node_ids": ["/rsi"]}).json()["nodes"]["/rsi"]
    stats = client.post(INSPECT, json={
        "cook_id": cook_id, "target": {"node_id": "/rsi"}, "attrs": ["@rsi"],
    }).json()["stats"]["@rsi"]
    assert node["min"] == pytest.approx(stats["min"])
    assert node["max"] == pytest.approx(stats["max"])
    assert node["nan_count"] == stats["nan_count"]
    # Every value is a real bar value or null (warmup).
    finite = [v for v in node["values"] if v is not None]
    assert finite and all(stats["min"] <= v <= stats["max"] for v in finite)
    assert node["values"][0] is None


def test_node_ids_and_points(client):
    cook_id = _backtest(client)["cook_id"]
    body = client.post(PREVIEW, json={"cook_id": cook_id, "node_ids": ["/rsi", "/nope"], "points": 32}).json()
    assert list(body["nodes"]) == ["/rsi"]
    assert len(body["nodes"]["/rsi"]["values"]) == 32


def test_points_out_of_range_is_422(client):
    cook_id = _backtest(client)["cook_id"]
    assert client.post(PREVIEW, json={"cook_id": cook_id, "points": 4}).status_code == 422
    assert client.post(PREVIEW, json={"cook_id": cook_id, "points": 5000}).status_code == 422


def test_bypassed_node_has_no_sparkline(client):
    cook_id = _backtest(client, graph=_graph(bypass_sma=True))["cook_id"]
    nodes = client.post(PREVIEW, json={"cook_id": cook_id}).json()["nodes"]
    assert "/sma" not in nodes and "/rsi" in nodes


def test_short_frame_still_gives_96_points(client, frame):
    frame["df"] = _intraday(40)
    cook_id = _backtest(client, interval="5m", start="2024-03-04", end="2024-03-05")["cook_id"]
    nodes = client.post(PREVIEW, json={"cook_id": cook_id}).json()["nodes"]
    assert len(nodes["/ticker"]["values"]) == 96


def test_preview_miss_cooks_from_the_graph_and_410_without_one(client):
    resp = client.post(PREVIEW, json={"cook_id": "ck_gone"})
    assert resp.status_code == 410 and resp.json() == {"detail": {"code": "cook_expired"}}
    body = client.post(PREVIEW, json={"cook_id": None, "graph": _graph(), "window": WINDOW}).json()
    assert body["cook_id"] in COOK_CACHE
    assert len(body["nodes"]["/rsi"]["values"]) == 96


# ---------------------------------------------------------------------------
# Decimation
# ---------------------------------------------------------------------------

def test_decimate_line_keeps_a_spike():
    col = np.zeros(10_000)
    col[5_123] = 50.0
    col[7_777] = -20.0
    values = decimate_line(col, 96)
    assert len(values) == 96
    assert 50.0 in values and -20.0 in values


def test_decimate_line_nan_buckets_are_null():
    col = np.full(960, np.nan)
    col[480:] = 1.0
    values = decimate_line(col, 96)
    assert values[:48] == [None] * 48
    assert values[48:] == [1.0] * 48


def test_decimate_bool_gives_shares():
    col = np.zeros(960, dtype=bool)
    col[:10] = True                  # 10 of the first bucket's 10 bars
    col[15:20] = True                # 5 of the second bucket's 10 bars
    values = decimate_bool(col, 96)
    assert values[0] == 1.0 and values[1] == 0.5 and values[2] == 0.0
