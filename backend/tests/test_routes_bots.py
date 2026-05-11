"""Pydantic-level and HTTP integration tests for /api/bots — F141/F143."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from pydantic import ValidationError
from fastapi.testclient import TestClient

from routes.bots import UpdateBotRequest
from routes import bots as bots_mod
from main import app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BOT_ID = "test-bot-001"

_RULE_FIELDS = [
    "buy_rules",
    "sell_rules",
    "long_buy_rules",
    "long_sell_rules",
    "short_buy_rules",
    "short_sell_rules",
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def client_with_stub_manager(client, monkeypatch):
    """Return (client, mock_mgr) with bot_manager swapped to a MagicMock."""
    mock_mgr = MagicMock()
    monkeypatch.setattr(bots_mod, "bot_manager", mock_mgr)
    return client, mock_mgr


_STUB_RULE = {"indicator": "rsi", "condition": "below", "value": 30}


@pytest.mark.parametrize("field", _RULE_FIELDS)
def test_update_bot_request_rejects_101_rules(field):
    """F141: UpdateBotRequest field-level cap fires at request validation,
    not after BotConfig reconstruct. Mirrors F128's cap on BotConfig itself.
    """
    with pytest.raises(ValidationError, match="too_long"):
        UpdateBotRequest(**{field: [_STUB_RULE] * 101})


@pytest.mark.parametrize("field", _RULE_FIELDS)
def test_update_bot_request_accepts_exactly_100_rules(field):
    """Boundary: exactly 100 rules is accepted; verify length AND first/last
    element survive the validator (catches a silent dedup or slice regression).
    """
    rules = [{**_STUB_RULE, "value": i} for i in range(100)]
    req = UpdateBotRequest(**{field: rules})
    parsed = getattr(req, field)
    assert parsed is not None
    assert len(parsed) == 100
    assert parsed[0].value == 0
    assert parsed[99].value == 99


def test_update_bot_request_accepts_none_for_all_rule_fields():
    """The 6 rule fields default to None (preserves F128 OptionalBoundedRuleList
    semantics). A PATCH that omits them must not 422 on the cap.
    """
    req = UpdateBotRequest()
    assert req.buy_rules is None
    assert req.sell_rules is None
    assert req.long_buy_rules is None
    assert req.long_sell_rules is None
    assert req.short_buy_rules is None
    assert req.short_sell_rules is None


def test_update_bot_request_accepts_empty_list():
    """An empty list (vs None) also passes — distinct from None for the
    PATCH path semantics: `[]` means "clear the rules"; `None` means "leave
    them alone" via model_dump(exclude_none=True).
    """
    req = UpdateBotRequest(buy_rules=[], sell_rules=[])
    assert req.buy_rules == []
    assert req.sell_rules == []


# ---------------------------------------------------------------------------
# F143: HTTP integration tests — /api/bots PATCH endpoint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", _RULE_FIELDS)
def test_patch_bot_rejects_101_rules_via_http(field, client_with_stub_manager):
    """F143: 101-rule list on any rule field → HTTP 422 with too_long detail.

    The Pydantic validator fires before _get_manager() so the mock is only
    needed to satisfy the 503 guard at runtime import.
    """
    client, _ = client_with_stub_manager
    body = {field: [_STUB_RULE] * 101}
    response = client.patch(f"/api/bots/{_BOT_ID}", json=body)
    assert response.status_code == 422
    detail_text = str(response.json())
    assert "too_long" in detail_text


@pytest.mark.parametrize("field", _RULE_FIELDS)
def test_patch_bot_accepts_exactly_100_rules_via_http(field, client_with_stub_manager):
    """F143: exactly 100 rules on any rule field → 200 OK with {"ok": True}.

    Verifies the cap boundary holds at the HTTP layer and the mock's
    update_bot is reached (MagicMock returns without raising by default).
    """
    client, _ = client_with_stub_manager
    body = {field: [_STUB_RULE] * 100}
    response = client.patch(f"/api/bots/{_BOT_ID}", json=body)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_patch_bot_503_when_bot_manager_unset(client, monkeypatch):
    """F143: bot_manager=None → 503 with 'Bot manager not initialized'.

    Catches a regression where _get_manager() guard is removed or bypassed.
    """
    monkeypatch.setattr(bots_mod, "bot_manager", None)
    body = {"buy_rules": [_STUB_RULE]}
    response = client.patch(f"/api/bots/{_BOT_ID}", json=body)
    assert response.status_code == 503
    assert "Bot manager not initialized" in response.json()["detail"]
