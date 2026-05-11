"""Pydantic-level tests for UpdateBotRequest — F141 rule-list caps."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from pydantic import ValidationError

from routes.bots import UpdateBotRequest


_STUB_RULE = {"indicator": "rsi", "condition": "below", "value": 30}


@pytest.mark.parametrize(
    "field",
    ["buy_rules", "sell_rules", "long_buy_rules", "long_sell_rules", "short_buy_rules", "short_sell_rules"],
)
def test_update_bot_request_rejects_101_rules(field):
    """F141: UpdateBotRequest field-level cap fires at request validation,
    not after BotConfig reconstruct. Mirrors F128's cap on BotConfig itself.
    """
    with pytest.raises(ValidationError, match="too_long"):
        UpdateBotRequest(**{field: [_STUB_RULE] * 101})


@pytest.mark.parametrize(
    "field",
    ["buy_rules", "sell_rules", "long_buy_rules", "long_sell_rules", "short_buy_rules", "short_sell_rules"],
)
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
