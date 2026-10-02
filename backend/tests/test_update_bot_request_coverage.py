"""Every BotConfig field is either editable through the bot PATCH body
(UpdateBotRequest) or on routes.bots.PATCH_DENYLIST with a reason
(F435 W5 5.D, plan D7).

Key Bugs Fixed, "silent drop of bot config fields": a request model that
copies BotConfig fields drops any field it forgets.  This test fails as soon
as a new BotConfig field is in neither list, so each new field gets a
decision.  The W5 graph fields (graph_id, graph_rev, graph_group,
graph_direction_mode) are on the denylist: they change only through
POST /api/bots/{id}/graph_update.

No bot is started and no order is placed: the manager is a mock.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.bots as bots_route
from bot_manager import BotConfig
from routes.bots import PATCH_DENYLIST, UpdateBotRequest

GRAPH_FIELDS = ("graph_id", "graph_rev", "graph_group", "graph_direction_mode")


def test_every_bot_config_field_is_patchable_or_denylisted():
    config_fields = set(BotConfig.model_fields)
    patchable = set(UpdateBotRequest.model_fields)
    denied = set(PATCH_DENYLIST)
    unclassified = config_fields - patchable - denied
    assert not unclassified, (
        f"BotConfig fields in neither UpdateBotRequest nor PATCH_DENYLIST: {sorted(unclassified)}")


def test_patch_body_and_denylist_do_not_overlap():
    assert not set(UpdateBotRequest.model_fields) & set(PATCH_DENYLIST)


def test_patch_body_names_only_bot_config_fields():
    # A PATCH field BotConfig does not have would be dropped by update_bot.
    assert set(UpdateBotRequest.model_fields) <= set(BotConfig.model_fields)


def test_denylist_names_only_bot_config_fields_and_gives_reasons():
    assert set(PATCH_DENYLIST) <= set(BotConfig.model_fields)
    for name, reason in PATCH_DENYLIST.items():
        assert isinstance(reason, str) and reason.strip(), name


@pytest.mark.parametrize("name", GRAPH_FIELDS)
def test_graph_fields_are_on_the_denylist(name):
    assert name in PATCH_DENYLIST
    assert name not in UpdateBotRequest.model_fields


@pytest.fixture
def client(monkeypatch):
    mgr = MagicMock()
    monkeypatch.setattr(bots_route, "bot_manager", mgr)
    app = FastAPI()
    app.include_router(bots_route.router)
    return TestClient(app), mgr


@pytest.mark.parametrize("name,value", [("graph_rev", 9), ("graph_id", "g_000000000001"),
                                        ("graph_group", "long_leg"),
                                        ("graph_direction_mode", "regime_switch")])
def test_patch_of_a_graph_field_is_refused_not_dropped(client, name, value):
    """A denylisted field in a PATCH is a 422 (UpdateBotRequest forbids
    unknown fields), never an "ok" that changed nothing."""
    tc, mgr = client
    r = tc.patch("/api/bots/b1", json={name: value})
    assert r.status_code == 422, r.text
    mgr.update_bot.assert_not_called()
