"""Tests for F199 — RequestDeadlineMiddleware.

Covers:
1. test_fast_route_passes_through            — under-deadline → 200
2. test_slow_route_returns_504               — over-deadline → 504 + body shape
3. test_exempt_route_no_deadline             — NO_DEADLINE path never times out
4. test_prefix_match_uses_longer_prefix      — longer prefix beats shorter prefix
5. test_default_deadline_applied_for_unknown_path — unknown path → default budget
6. test_response_already_started_no_double_send   — no 504 after response.start
7. test_websocket_scope_bypassed             — non-http scope bypasses entirely
8. test_non_body_method_still_timed_out      — GET still hits deadline (unlike body-size MW)
"""

from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from middleware import RequestDeadlineMiddleware, NO_DEADLINE


# ---------------------------------------------------------------------------
# Event-loop isolation (Python 3.12 compatibility)
# ---------------------------------------------------------------------------
# The conftest.py _f139_ensure_event_loop fixture sets asyncio.set_event_loop(None)
# after EVERY test in backend/tests/.  Tests in backend/research/ do not have an
# equivalent setup fixture, so they receive no current event loop and
# asyncio.get_event_loop() raises RuntimeError in Python 3.12+.
#
# This module-scoped autouse fixture installs a fresh loop after all tests in
# this module complete, so that the subsequent research/test_premise_run.py
# tests (which use asyncio.get_event_loop().run_until_complete) can proceed.
# The per-test conftest fixture still manages the loop for individual tests.

@pytest.fixture(scope="module", autouse=True)
def _restore_loop_after_module():
    """Install a fresh event loop after this module's tests complete.

    Counteracts the conftest _f139_ensure_event_loop finalizer
    (asyncio.set_event_loop(None)) so subsequent test modules in different
    directories (backend/research/) still have a usable loop.
    """
    yield  # let all tests in this module run
    # Restore a fresh loop so code in the next test module that calls
    # asyncio.get_event_loop() does not raise RuntimeError.
    asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_app(
    route_deadlines: dict[str, float | None] | None = None,
    default_deadline: float = 30.0,
) -> FastAPI:
    """Build a minimal FastAPI app with RequestDeadlineMiddleware.

    Adds two routes:
    - GET /fast  — returns immediately
    - GET /slow  — sleeps 0.2 s (used with a tight deadline to trigger 504)
    - GET /exempt — listed in route_deadlines with None; should never 504
    """
    app = FastAPI()
    app.add_middleware(
        RequestDeadlineMiddleware,
        route_deadlines=route_deadlines if route_deadlines is not None else {},
        default_deadline=default_deadline,
    )

    @app.get("/fast")
    async def fast_route():
        return {"ok": True}

    @app.get("/slow")
    async def slow_route():
        await asyncio.sleep(0.2)
        return {"ok": True}

    @app.get("/exempt")
    async def exempt_route():
        await asyncio.sleep(0.2)
        return {"ok": True}

    return app


# ---------------------------------------------------------------------------
# Test 1: fast route passes through
# ---------------------------------------------------------------------------

def test_fast_route_passes_through():
    """A route that completes well within the deadline should return 200."""
    app = _build_app(
        route_deadlines={"/fast": 5.0},
        default_deadline=5.0,
    )
    client = TestClient(app)
    resp = client.get("/fast")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Test 2: slow route returns 504 with correct body shape
# ---------------------------------------------------------------------------

def test_slow_route_returns_504():
    """A route that exceeds the deadline returns 504 with a detail body."""
    # /slow sleeps 0.2 s; budget is 0.05 s → must timeout.
    app = _build_app(
        route_deadlines={"/slow": 0.05},
        default_deadline=5.0,
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/slow")
    assert resp.status_code == 504
    body = resp.json()
    assert "detail" in body
    # The detail should mention the deadline value (0s after .0f formatting)
    assert "deadline exceeded" in body["detail"].lower()


# ---------------------------------------------------------------------------
# Test 3: exempt route (NO_DEADLINE) never triggers 504
# ---------------------------------------------------------------------------

def test_exempt_route_no_deadline():
    """A route mapped to NO_DEADLINE passes through regardless of sleep duration."""
    app = _build_app(
        route_deadlines={"/exempt": NO_DEADLINE},
        default_deadline=0.05,  # tiny global default — would fire for non-exempt routes
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/exempt")
    # Must complete successfully (0.2 s sleep, but exempt from deadline)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 4: prefix matching picks longer prefix
# ---------------------------------------------------------------------------

def test_prefix_match_uses_longer_prefix():
    """Longest-prefix semantics: /api/backtest/walk_forward (620 s) should beat
    /api/backtest (90 s) for a path like /api/backtest/walk_forward/run.

    Verified by checking _resolve_deadline directly, and by confirming that a
    short path matching only the shorter prefix IS timed out by a tight budget.
    """
    deadlines = {
        "/api/backtest":                  0.05,   # tight — would fire
        "/api/backtest/walk_forward":     620.0,  # generous — should NOT fire
    }
    mw = RequestDeadlineMiddleware(
        app=None,  # type: ignore[arg-type]
        route_deadlines=deadlines,
        default_deadline=5.0,
    )
    # /api/backtest/walk_forward/run → longest match is walk_forward prefix
    assert mw._resolve_deadline("/api/backtest/walk_forward/run") == 620.0
    # /api/backtest (exact) → exact match
    assert mw._resolve_deadline("/api/backtest") == 0.05
    # /api/backtest/results → only matches /api/backtest prefix
    assert mw._resolve_deadline("/api/backtest/results") == 0.05


# ---------------------------------------------------------------------------
# Test 5: default deadline for unknown path
# ---------------------------------------------------------------------------

def test_default_deadline_applied_for_unknown_path():
    """A path not in route_deadlines falls back to default_deadline."""
    mw = RequestDeadlineMiddleware(
        app=None,  # type: ignore[arg-type]
        route_deadlines={"/known": 10.0},
        default_deadline=42.0,
    )
    assert mw._resolve_deadline("/unknown") == 42.0
    assert mw._resolve_deadline("/api/anything") == 42.0


# ---------------------------------------------------------------------------
# Test 6: response already started — no double send
# ---------------------------------------------------------------------------

def test_response_already_started_no_double_send():
    """If the handler sends http.response.start before the deadline fires, the
    middleware must NOT attempt to send a 504 start afterward (ASGI violation).

    Verified by collecting all outbound ASGI messages and asserting that
    http.response.start appears exactly once.
    """
    sent_messages: list[dict] = []

    async def fake_send(message: dict) -> None:
        sent_messages.append(message)

    async def fake_receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def slow_handler(scope, receive, send):
        # Send response.start immediately, then sleep past deadline.
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await asyncio.sleep(0.2)  # sleep past the 0.05 s deadline
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    mw = RequestDeadlineMiddleware(
        app=slow_handler,
        route_deadlines={"/test": 0.05},
        default_deadline=5.0,
    )
    scope = {"type": "http", "method": "GET", "path": "/test"}
    asyncio.run(mw(scope, fake_receive, fake_send))

    start_messages = [m for m in sent_messages if m.get("type") == "http.response.start"]
    # Must have exactly one response.start — no 504 injected after headers sent.
    assert len(start_messages) == 1
    # The only start message should be the original 200, not a 504.
    assert start_messages[0]["status"] == 200


# ---------------------------------------------------------------------------
# Test 7: WebSocket scope bypassed
# ---------------------------------------------------------------------------

def test_websocket_scope_bypassed():
    """Non-http ASGI scopes (websocket, lifespan) must bypass deadline entirely."""
    downstream_calls: list[str] = []

    async def fake_receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def fake_send(message: dict) -> None:
        pass

    async def downstream(scope, receive, send):
        downstream_calls.append(scope["type"])

    mw = RequestDeadlineMiddleware(
        app=downstream,
        route_deadlines={},
        default_deadline=0.001,  # essentially zero — would fire if http
    )
    scope = {"type": "websocket", "path": "/ws"}
    asyncio.run(mw(scope, fake_receive, fake_send))
    # Downstream was called and scope bypassed the deadline logic.
    assert downstream_calls == ["websocket"]


# ---------------------------------------------------------------------------
# Test 8: GET (non-body method) still timed out
# ---------------------------------------------------------------------------

def test_non_body_method_still_timed_out():
    """Unlike BodySizeLimitMiddleware (which exempts non-POST/PUT/PATCH),
    RequestDeadlineMiddleware applies to ALL HTTP methods including GET."""
    # /slow sleeps 0.2 s; budget is 0.05 s.
    app = _build_app(
        route_deadlines={"/slow": 0.05},
        default_deadline=5.0,
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/slow")
    assert resp.status_code == 504


# ---------------------------------------------------------------------------
# Bonus: env override parsing (GLOBAL_DEFAULT_DEADLINE_SECS driven by env)
# ---------------------------------------------------------------------------

def test_env_override_knob(monkeypatch):
    """STRATEGYLAB_REQUEST_DEADLINE_SECS overrides GLOBAL_DEFAULT_DEADLINE_SECS
    when the module is (re-)imported.  Verify by re-importing after monkeypatching."""
    import importlib
    import middleware as mw_module

    monkeypatch.setenv("STRATEGYLAB_REQUEST_DEADLINE_SECS", "123.0")
    importlib.reload(mw_module)
    assert mw_module.GLOBAL_DEFAULT_DEADLINE_SECS == 123.0

    # Restore original state to avoid leaking into other tests.
    monkeypatch.delenv("STRATEGYLAB_REQUEST_DEADLINE_SECS", raising=False)
    importlib.reload(mw_module)
