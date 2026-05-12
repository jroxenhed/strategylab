import asyncio
import pytest

# ---------------------------------------------------------------------------
# Shared stub rule (F142) — canonical form used across all test files that
# need a syntactically valid Rule dict. indicator='rsi' is a registered
# indicator; 'price' was historically used in test_models.py but could break
# if Rule.indicator ever gains an allowlist validator (F106).
# ---------------------------------------------------------------------------
_STUB_RULE: dict = {"indicator": "rsi", "condition": "above", "value": 50}
_STUB_RULES_100: list = [_STUB_RULE for _ in range(100)]


@pytest.fixture(autouse=True)
def _f139_ensure_event_loop():
    """Ensure each test has a thread-default event loop set.

    Without this, `asyncio.run()` calls in prior tests close their internal
    loop and leave the main thread with no default. ib_insync's `eventkit`
    package calls `asyncio.get_event_loop()` at module-import time
    (eventkit/util.py:24), which raises RuntimeError on Python 3.10+ when
    no loop is set. This contaminates `test_ibkr_provider_submit_market_order`
    when it runs after async tests — the lone remaining suite failure that
    blocked the F120/F40/F46/F107/F108/F134 sweep.

    Strategy: set a fresh default loop before each test; close + clear after.
    pytest-asyncio 1.3.0 (asyncio_mode=auto) manages its own loop via
    event_loop_policy — not the deprecated event_loop fixture — so this
    fixture does not conflict with it.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        loop.close()
        # Clear the thread default. The earlier draft of this fixture left
        # the closed loop visible on the thread, on the theory pytest-asyncio
        # would prefer "previous loop existed" over None. Morning review
        # (correctness + kieran-python, both P1 ~0.72) pointed out that
        # pytest-asyncio 1.3.0 manages its loop via event_loop_policy, not
        # by inspecting the thread default, so a closed-loop sentinel buys
        # nothing and any inter-test code calling asyncio.get_event_loop()
        # would silently receive the closed loop. set_event_loop(None) is
        # the conservative move — no stale state across tests.
        asyncio.set_event_loop(None)
