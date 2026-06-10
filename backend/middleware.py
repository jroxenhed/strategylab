"""F86: HTTP request body size limit middleware.

Pydantic Field(max_length=...) caps run AFTER FastAPI reads the entire
request body into memory. A multi-GB POST to any endpoint would OOM long
before per-field validation gets a chance to reject it. This middleware
rejects oversized bodies via the Content-Length header up front; if the
client sent Transfer-Encoding: chunked (no header), the body is counted
through receive() with the same cap before the app sees a single byte.

Smuggling-resistance hardening (build 24 adversarial review):
- Duplicate Content-Length headers are rejected up-front (RFC 7230 §3.3.2).
- When Transfer-Encoding is present the Content-Length fast path is skipped
  and the slow path counts actual streamed bytes — closes the
  CL=0+chunked-body and TE+CL coexistence desync variants.

F199: RequestDeadlineMiddleware — per-route and global-default HTTP deadlines.
See class docstring for trade-offs and env-override knob.
"""

import asyncio
import json
import os
from typing import Final
from starlette.types import ASGIApp, Scope, Receive, Send

DEFAULT_MAX_BYTES = 1_048_576  # 1 MB


def parse_max_body_env(value: str | None) -> int:
    """Parse STRATEGYLAB_MAX_BODY_BYTES env value. Raises ValueError on invalid input.

    Returns DEFAULT_MAX_BYTES when value is None or empty; raises ValueError for
    non-numeric or non-positive input so callers can log + fall back uniformly.
    """
    if not value:
        return DEFAULT_MAX_BYTES
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"STRATEGYLAB_MAX_BODY_BYTES must be positive, got {parsed}")
    return parsed


class BodySizeLimitMiddleware:
    """Limits HTTP request body size via Content-Length fast path with chunked-body slow path fallback.

    Pure ASGI (not BaseHTTPMiddleware) to avoid lifespan interaction issues.
    """

    BODY_METHODS = frozenset(("POST", "PUT", "PATCH"))

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Non-HTTP scopes (WebSocket, lifespan) bypass the body cap — they don't
        # have HTTP request bodies in the same shape. If WebSocket routes are
        # added later, design a separate frame-size limit; do NOT assume this middleware covers them.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method not in self.BODY_METHODS:
            await self.app(scope, receive, send)
            return

        # Walk headers once, collecting every Content-Length value and noting
        # whether Transfer-Encoding is present. ASGI lowercases header names.
        cl_values: list[bytes] = []
        has_transfer_encoding = False
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                cl_values.append(value)
            elif name == b"transfer-encoding":
                # F112: 'identity' is the no-op encoding (RFC 7230 §4) — treat as
                # absent so the Content-Length fast path still applies.
                if value.strip().lower() != b"identity":
                    has_transfer_encoding = True

        # F111: reject Content-Length with leading/trailing whitespace (RFC 7230 §3.2.4).
        # `b' 50'` or `b'50 '` would otherwise parse via int() but signals a malformed
        # or smuggled header — reject up-front.
        for v in cl_values:
            if v.strip() != v:
                await _reply(send, 400, "Invalid Content-Length")
                return

        # Reject duplicate Content-Length outright — RFC 7230 §3.3.2 says
        # differing values MUST 400 and even matching values are a smuggling
        # surface, so we don't accept them. uvicorn/h11 generally reject these
        # upstream; the explicit check is belt-and-suspenders.
        if len(cl_values) > 1:
            await _reply(send, 400, "Invalid Content-Length")
            return

        # Fast path: a single Content-Length header AND no Transfer-Encoding.
        # If TE is present the CL value is ignored per RFC 7230 §3.3.3 and we
        # count actual bytes via the slow path — closes the CL+TE coexistence
        # smuggling variant.
        if cl_values and not has_transfer_encoding:
            try:
                declared = int(cl_values[0])
            except ValueError:
                await _reply(send, 400, "Invalid Content-Length")
                return
            if declared < 0:
                await _reply(send, 400, "Invalid Content-Length")
                return
            if declared > self.max_bytes:
                await _reply(
                    send, 413,
                    f"Request body too large (max {self.max_bytes} bytes)",
                )
                return
            await self.app(scope, receive, send)
            return

        # Slow path: no Content-Length, or both CL and TE present. Buffer with
        # a cap and replay. Drained from receive() one chunk at a time so we
        # short-circuit on the first overflow byte rather than after the whole
        # body lands.
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            mtype = message.get("type")
            if mtype == "http.disconnect":
                return
            if mtype != "http.request":
                continue
            body = message.get("body", b"") or b""
            total += len(body)
            if total > self.max_bytes:
                await _reply(
                    send, 413,
                    f"Request body too large (max {self.max_bytes} bytes)",
                )
                return
            chunks.append(body)
            more = message.get("more_body", False)

        buffered = b"".join(chunks)
        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            # Body fully consumed. Returning an empty terminal body matches the
            # ASGI contract (a fully-drained receive() should not block) — the
            # original receive() is drained and falling through would hang
            # waiting for an http.disconnect that may never arrive.
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


# ---------------------------------------------------------------------------
# F199: RequestDeadlineMiddleware
# ---------------------------------------------------------------------------

#: Sentinel — assign to a path in ROUTE_DEADLINES to exempt it entirely.
#: KP-03: Final[None] makes the intent explicit in type signatures and prevents
#: accidental assignment (e.g. ROUTE_DEADLINES['/foo'] = unset_var).
NO_DEADLINE: Final[None] = None

#: Per-path deadline table.  Longest-prefix wins for paths not in the dict.
#: Streaming / long-lived routes that self-manage timeouts internally must be
#: listed here with NO_DEADLINE to prevent the middleware from cancelling them.
ROUTE_DEADLINES: dict[str, float | None] = {
    # Streaming WFA — exempt; self-manages via _WFA_TIMEOUT_SECS
    "/api/backtest/walk_forward/stream": NO_DEADLINE,
    # WFA (non-stream): 20 s headroom above _WFA_TIMEOUT_SECS=600
    "/api/backtest/walk_forward":        620.0,
    # Optimizer: 10 s headroom above _TIMEOUT_SECS=60
    "/api/backtest/optimize":             70.0,
    # Sweep
    "/api/backtest/sweep":               120.0,
    # Quick batch: already has internal deadline; belt-and-suspenders
    "/api/backtest/quick/batch":          30.0,
    # Quick (single)
    "/api/backtest/quick":                15.0,
    # Standard backtest (90 s covers wide date ranges + slow yfinance fetch)
    "/api/backtest":                      90.0,
    # Turnaround scan
    "/api/turnaround/scan":              180.0,
    # Trading scan
    "/scan":                              60.0,
}

#: Global fallback applied to any path NOT matched by ROUTE_DEADLINES.
#: Override at startup via STRATEGYLAB_REQUEST_DEADLINE_SECS (seconds, float).
_env_default = os.environ.get("STRATEGYLAB_REQUEST_DEADLINE_SECS")
try:
    GLOBAL_DEFAULT_DEADLINE_SECS: float = float(_env_default) if _env_default else 60.0
except (ValueError, TypeError):
    GLOBAL_DEFAULT_DEADLINE_SECS = 60.0


class RequestDeadlineMiddleware:
    """Pure-ASGI middleware that wraps each HTTP request in asyncio.wait_for.

    Route-specific deadlines are read from *route_deadlines* using an exact
    match first, then a longest-prefix match.  Paths mapped to ``None``
    (NO_DEADLINE) pass through without any timeout — use this for streaming
    responses and other long-lived connections that self-manage their own
    timeouts internally.

    Known limitation (sync routes):
        FastAPI dispatches sync route handlers to a threadpool via
        run_in_executor.  ``asyncio.wait_for`` wraps the coroutine that
        *awaits* that executor future, so timing out bounds CLIENT-VISIBLE
        latency — the threadpool thread itself is NOT cancelled and continues
        running until it finishes naturally.  This is an unavoidable
        consequence of Python's sync-handler model.  Under sustained load,
        timed-out-but-still-running threads will hold threadpool slots until
        they complete; this is a known and accepted trade-off.

    Known limitation (partial results):
        Unlike ``/api/backtest/quick/batch`` (which returns per-symbol
        ``error="deadline exceeded"`` rows on timeout), a middleware 504
        returns no partial results.  Non-batch routes have no partial-results
        format, so this is intentional.

    Double-send protection:
        A ``send`` wrapper tracks whether ``http.response.start`` has already
        been sent.  If the handler is mid-write when the timeout fires,
        sending a second 504 start would violate the ASGI protocol.  The
        guard skips the 504 in that case and lets the connection close
        naturally.

    Env override:
        ``STRATEGYLAB_REQUEST_DEADLINE_SECS`` overrides ``default_deadline``
        at module import time (affects the module-level
        ``GLOBAL_DEFAULT_DEADLINE_SECS`` constant).  Per-route budgets are
        code-only constants for now.
    """

    def __init__(
        self,
        app: ASGIApp,
        route_deadlines: dict[str, float | None] | None = None,
        default_deadline: float = GLOBAL_DEFAULT_DEADLINE_SECS,
    ) -> None:
        self.app = app
        self.route_deadlines: dict[str, float | None] = (
            route_deadlines if route_deadlines is not None else ROUTE_DEADLINES
        )
        self.default_deadline = default_deadline

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Non-HTTP scopes (WebSocket, lifespan) bypass deadline entirely.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        deadline = self._resolve_deadline(path)
        if deadline is None:
            # Exempt path — pass through unconditionally.
            await self.app(scope, receive, send)
            return

        # Wrap send to detect whether response.start has been sent.
        response_started = False

        async def send_with_flag(message: dict) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await asyncio.wait_for(
                self.app(scope, receive, send_with_flag),
                timeout=deadline,
            )
        except asyncio.TimeoutError:
            if not response_started:
                await _reply(
                    send,
                    504,
                    f"Request deadline exceeded ({deadline:.0f}s)",
                )
            # else: partial response already in flight — can't 504 now;
            # connection will close and the client will see a truncated
            # response, which is the best we can do without violating ASGI.

    def _resolve_deadline(self, path: str) -> float | None:
        """Return the deadline for *path*: exact match → longest prefix → default."""
        # Exact match.
        if path in self.route_deadlines:
            return self.route_deadlines[path]
        # Longest-prefix match.
        best_len = -1
        best_val: float | None = self.default_deadline
        for prefix, val in self.route_deadlines.items():
            if path.startswith(prefix) and len(prefix) > best_len:
                best_len = len(prefix)
                best_val = val
        return best_val


async def _reply(send: Send, status: int, detail: str) -> None:
    payload = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": payload, "more_body": False})
