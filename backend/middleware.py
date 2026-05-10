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
"""

import json

DEFAULT_MAX_BYTES = 1_048_576  # 1 MB


class BodySizeLimitMiddleware:
    """ASGI middleware that rejects HTTP request bodies exceeding max_bytes.

    Pure ASGI rather than BaseHTTPMiddleware to avoid the well-known
    BaseHTTPMiddleware/lifespan interactions and so the rejection happens
    before any FastAPI route dispatch.
    """

    BODY_METHODS = frozenset(("POST", "PUT", "PATCH"))

    def __init__(self, app, max_bytes: int = DEFAULT_MAX_BYTES):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
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
                has_transfer_encoding = True

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


async def _reply(send, status: int, detail: str) -> None:
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
