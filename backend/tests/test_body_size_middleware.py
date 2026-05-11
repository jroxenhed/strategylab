"""Tests for F86 — HTTP request body size limit middleware.

Verifies:
- Bodies under the cap pass through (regression guard).
- Bodies over the cap are rejected with 413 before any route handler.
- GET / HEAD / OPTIONS / DELETE pass through regardless of any size signal.
- Chunked transfer (no Content-Length) is counted via the slow path.
- A malformed Content-Length header → 400.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware import BodySizeLimitMiddleware, DEFAULT_MAX_BYTES, parse_max_body_env


def _build_app(max_bytes: int = 1024) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)

    @app.post("/echo")
    async def echo(payload: dict):
        return {"len": len(json.dumps(payload))}

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return app


def test_small_post_passes():
    client = TestClient(_build_app(max_bytes=1024))
    resp = client.post("/echo", json={"key": "value"})
    assert resp.status_code == 200


def test_post_over_cap_returns_413():
    """Content-Length header fast path: oversized body short-circuits to 413
    with the expected JSON detail, and the route handler never runs."""
    client = TestClient(_build_app(max_bytes=128))
    big_payload = {"k": "x" * 500}
    resp = client.post("/echo", json=big_payload)
    assert resp.status_code == 413
    body = resp.json()
    assert "Request body too large" in body["detail"]


def test_get_request_unaffected_by_cap():
    """Body-less methods never trigger the cap, even with a giant URL/header."""
    client = TestClient(_build_app(max_bytes=4))
    resp = client.get("/ping")
    assert resp.status_code == 200


def test_body_exactly_at_cap_accepted():
    """Boundary: when the declared body size equals max_bytes, the middleware
    uses strict `>` not `>=`, so at-cap requests must pass through.

    Catches the off-by-one regression that would otherwise reject the largest
    legitimate request.
    """
    # _build_app's `/echo` parses JSON. Construct a payload whose serialized
    # form is exactly 64 bytes to test the boundary precisely.
    body = '{"k":"' + "x" * 56 + '"}'
    assert len(body) == 64
    client = TestClient(_build_app(max_bytes=64))
    resp = client.post(
        "/echo",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text


def test_body_one_byte_over_cap_rejected():
    """Boundary inverse: at max_bytes+1 the strict-inequality cap fires."""
    body = '{"k":"' + "x" * 57 + '"}'
    assert len(body) == 65
    client = TestClient(_build_app(max_bytes=64))
    resp = client.post(
        "/echo",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413


def test_duplicate_content_length_header_rejected():
    """Adversarial: two Content-Length headers (request smuggling) → 400.

    TestClient won't send duplicate headers itself; drive ASGI directly.
    """
    received_messages: list = []

    async def fake_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def fake_send(message):
        received_messages.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("downstream must not run on duplicate CL")

    mw = BodySizeLimitMiddleware(downstream, max_bytes=1024)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"content-length", b"100"),
            (b"content-length", b"200"),
        ],
    }
    asyncio.run(mw(scope, fake_receive, fake_send))
    assert received_messages[0]["status"] == 400
    body = b"".join(m.get("body", b"") for m in received_messages if m["type"] == "http.response.body")
    assert b"Invalid Content-Length" in body


def test_transfer_encoding_forces_slow_path_overrules_content_length():
    """Adversarial: when Transfer-Encoding is present alongside Content-Length,
    the middleware ignores CL and counts the streamed body via the slow path.

    Closes the CL=0+chunked-body and TE+CL coexistence desync variants.
    """
    received_messages: list = []
    chunks = iter([
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"y" * 60, "more_body": False},
    ])

    async def fake_receive():
        return next(chunks)

    async def fake_send(message):
        received_messages.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("downstream must not run when slow path rejects")

    mw = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"content-length", b"0"),                # liar — would bypass without TE check
            (b"transfer-encoding", b"chunked"),
        ],
    }
    asyncio.run(mw(scope, fake_receive, fake_send))
    statuses = [m["status"] for m in received_messages if m["type"] == "http.response.start"]
    assert statuses == [413]


def test_replay_receive_second_call_returns_empty_body_not_hang():
    """Replay-receive contract: after the buffered body is delivered, any
    second receive() call returns a terminal empty body so a downstream that
    re-reads can't hang waiting for an http.disconnect that may never arrive.
    """
    chunk = iter([{"type": "http.request", "body": b"hello", "more_body": False}])

    async def fake_receive():
        return next(chunk)

    async def fake_send(message):
        pass

    second_call_result = []

    async def downstream(scope, receive, send):
        first = await receive()
        second = await receive()
        second_call_result.append(second)

    mw = BodySizeLimitMiddleware(downstream, max_bytes=1024)
    scope = {"type": "http", "method": "POST", "headers": []}
    asyncio.run(mw(scope, fake_receive, fake_send))
    assert second_call_result == [
        {"type": "http.request", "body": b"", "more_body": False}
    ]


def test_malformed_content_length_returns_400():
    """A Content-Length that doesn't parse as int → 400. TestClient won't send
    a malformed header itself, so we drive ASGI directly."""
    received_messages: list = []

    async def fake_receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def fake_send(message):
        received_messages.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("downstream must not be called for malformed CL")

    mw = BodySizeLimitMiddleware(downstream, max_bytes=1024)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-length", b"not-a-number")],
    }
    asyncio.run(mw(scope, fake_receive, fake_send))
    assert received_messages[0]["status"] == 400
    body = b"".join(m.get("body", b"") for m in received_messages if m["type"] == "http.response.body")
    assert b"Invalid Content-Length" in body


def test_negative_content_length_returns_400():
    received_messages: list = []

    async def fake_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def fake_send(message):
        received_messages.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("downstream must not be called for negative CL")

    mw = BodySizeLimitMiddleware(downstream, max_bytes=1024)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-length", b"-1")],
    }
    asyncio.run(mw(scope, fake_receive, fake_send))
    assert received_messages[0]["status"] == 400


def test_chunked_transfer_overflow_returns_413():
    """Slow path: Content-Length absent. The middleware counts bytes through
    receive() and short-circuits as soon as the cumulative size exceeds the cap."""
    received_messages: list = []

    chunks = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"y" * 60, "more_body": True},
        {"type": "http.request", "body": b"z" * 60, "more_body": False},
    ]
    chunk_iter = iter(chunks)

    async def fake_receive():
        try:
            return next(chunk_iter)
        except StopIteration:
            return {"type": "http.disconnect"}

    async def fake_send(message):
        received_messages.append(message)

    async def downstream(scope, receive, send):
        # If we got here, the cap failed.
        raise AssertionError("downstream must not be reached on overflow")

    mw = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = {"type": "http", "method": "POST", "headers": []}
    asyncio.run(mw(scope, fake_receive, fake_send))
    statuses = [m["status"] for m in received_messages if m["type"] == "http.response.start"]
    assert statuses == [413]


def test_chunked_transfer_under_cap_replays_body():
    """Slow path: when body is under the cap, the middleware replays the buffered
    bytes to the downstream app exactly once."""
    received_body: list = []

    chunks = [
        {"type": "http.request", "body": b'{"x":', "more_body": True},
        {"type": "http.request", "body": b'1}', "more_body": False},
    ]
    chunk_iter = iter(chunks)

    async def fake_receive():
        try:
            return next(chunk_iter)
        except StopIteration:
            return {"type": "http.disconnect"}

    async def fake_send(message):
        pass

    async def downstream(scope, receive, send):
        msg = await receive()
        received_body.append(msg)

    mw = BodySizeLimitMiddleware(downstream, max_bytes=1024)
    scope = {"type": "http", "method": "POST", "headers": []}
    asyncio.run(mw(scope, fake_receive, fake_send))
    assert received_body == [{"type": "http.request", "body": b'{"x":1}', "more_body": False}]


def test_constructor_rejects_nonpositive_max_bytes():
    with pytest.raises(ValueError):
        BodySizeLimitMiddleware(lambda scope, r, s: None, max_bytes=0)
    with pytest.raises(ValueError):
        BodySizeLimitMiddleware(lambda scope, r, s: None, max_bytes=-1)


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "DELETE"])
def test_non_body_method_bypasses_cap_even_with_huge_content_length(method):
    """F107: methods outside BODY_METHODS short-circuit to downstream regardless
    of Content-Length. The frozenset is `{POST, PUT, PATCH}` — everything else
    (GET test already exists at module level) must pass through.

    Drives ASGI directly because TestClient strips request bodies on these
    methods, masking the Content-Length signal.
    """
    downstream_calls: list[str] = []

    async def fake_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def fake_send(message):
        pass

    async def downstream(scope, receive, send):
        downstream_calls.append(scope["method"])

    mw = BodySizeLimitMiddleware(downstream, max_bytes=100)
    scope = {
        "type": "http",
        "method": method,
        "headers": [(b"content-length", b"10000")],
    }
    asyncio.run(mw(scope, fake_receive, fake_send))
    assert downstream_calls == [method], (
        f"{method} should pass through even with Content-Length=10000 > max_bytes=100"
    )


def test_parse_max_body_env_returns_default_when_unset():
    """F108: empty/None env value → DEFAULT_MAX_BYTES (no warning)."""
    assert parse_max_body_env(None) == DEFAULT_MAX_BYTES
    assert parse_max_body_env("") == DEFAULT_MAX_BYTES


def test_parse_max_body_env_accepts_valid_override():
    """F108: a positive integer string parses to that exact value."""
    assert parse_max_body_env("2048") == 2048
    assert parse_max_body_env("1") == 1


def test_parse_max_body_env_raises_on_non_numeric():
    """F108: garbage env value surfaces as ValueError so main.py can warn + fall back."""
    with pytest.raises(ValueError):
        parse_max_body_env("not-a-number")
    with pytest.raises(ValueError):
        parse_max_body_env("1.5")  # int() rejects floats too


def test_parse_max_body_env_raises_on_nonpositive():
    """F108: zero or negative env value is invalid (matches BodySizeLimitMiddleware constructor)."""
    with pytest.raises(ValueError):
        parse_max_body_env("0")
    with pytest.raises(ValueError):
        parse_max_body_env("-100")
