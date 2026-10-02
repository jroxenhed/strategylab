"""Graph library routes: reusable sub-network assets (F435 W6 item 6.B).

    GET    /api/graph_library                   -> 200 {"assets": [AssetListItem]}
    GET    /api/graph_library/{name}/{version}  -> 200 AssetFile | 404
    POST   /api/graph_library                   -> 201 AssetFile (version = latest + 1)
    DELETE /api/graph_library/{name}/{version}  -> 204 | 404

The POST body is {"name", "description", "network", "promoted",
"interface"?, "palette"?}.  Each version is its own file and never changes
(see nodebuilder/storage.py AssetLibrary).  A DELETE does not touch graphs:
a graph that uses the deleted version then shows ``asset_missing`` when it
compiles.  Bots are immune: spawn bakes the asset's nodes into the bot's
own copy of the graph (routes/graphs.py bake_in_library).

Errors:
  - 400 in the plan section 4.4 shape ({"detail", "node_id", "code",
    "diagnostics"}) with code ``name_invalid`` (the name does not match
    ``^[a-z_][a-z0-9_]{0,63}$``), ``promoted_invalid`` (a promoted param
    whose target is not a node param inside the asset, a bad type, a
    duplicate name or a default of the wrong kind), ``request_invalid`` (an
    empty network, a long description) or a graph code when the network
    does not parse.
  - 404 when there is no such asset version.
  - 409 {"detail": {"code": "asset_corrupt", "message": <sentence>,
    "detail": <the same sentence>}} when the stored file cannot be read
    (``detail`` kept for parity with graph_corrupt; the client reads
    ``message``).
  - 409 {"detail": {"code": "asset_version_taken", "message"}} when the new
    version's file appeared under the save (another writer); nothing was
    replaced, so saving again gives the next number.
  - 500 {"detail": {"code": "library_io_error", "message"}} when a file
    cannot be written or removed (a full disk, a permission problem).
  - 413 when the body is over 1 MB (the app-wide body cap).  422 for a
    badly shaped body.

All file work runs in the thread pool, never on the event loop.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from nodebuilder.diagnostics import Diagnostic, error_body
from nodebuilder.models import GraphValidationError
from nodebuilder.storage import (
    AssetCorruptError,
    AssetNotFoundError,
    AssetVersionTakenError,
    InvalidAssetError,
    asset_list,
    get_library,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph_library", tags=["graph_library"])

# The app-wide body cap (middleware DEFAULT_MAX_BYTES) also applies here;
# an asset is one network, far below it.
MAX_BODY_BYTES = 1024 * 1024

# Errors a network that does not parse can raise from Graph.model_validate.
_PARSE_ERRORS = (GraphValidationError, ValidationError, ValueError, TypeError)


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


class AttrDeclBody(BaseModel):
    """One attribute the asset reads or writes (plan W6 AttrDecl)."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=64)
    klass: Literal["point", "detail"] = Field(alias="class")
    dtype: str = Field(min_length=1, max_length=32)


class InterfaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reads: list[AttrDeclBody] = Field(default_factory=list, max_length=256)
    writes: list[AttrDeclBody] = Field(default_factory=list, max_length=256)


class PaletteBody(BaseModel):
    """A Rules palette entry (decisions-pre W6)."""
    model_config = ConfigDict(extra="forbid")

    category: Literal["rules"]
    label: str = Field(min_length=1, max_length=64)
    glyph: str = Field(min_length=1, max_length=4)


class NetworkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: dict[str, Any]
    wires: list[Any] = Field(default_factory=list)


class SaveAssetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The name pattern is checked by the store (a 400 name_invalid the save
    # dialog can show), not here.
    name: str = Field(max_length=200)
    description: str = ""
    network: NetworkBody
    # Each promoted param is checked against the network by the store
    # (400 promoted_invalid), so it stays a plain object here.
    promoted: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    interface: Optional[InterfaceBody] = None
    palette: Optional[PaletteBody] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_body(request: Request) -> SaveAssetBody:
    """Read the raw body, enforce the size limit, then parse it."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=413, detail=f"Request body too large (max {MAX_BODY_BYTES} bytes)"
        )
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body",), "msg": "Body is not valid JSON", "input": None}]
        )
    try:
        return SaveAssetBody.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        )


def _invalid(exc: InvalidAssetError) -> JSONResponse:
    """400 in the plan 4.4 shape for an asset the store refused."""
    diag = Diagnostic(code=exc.code, message=exc.message, node_id=exc.node_id, param=exc.param)
    return JSONResponse(status_code=400, content={
        "detail": exc.message,
        "node_id": exc.node_id,
        "code": exc.code,
        "diagnostics": [diag.model_dump()],
    })


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Asset not found")


def _corrupt(exc: AssetCorruptError) -> JSONResponse:
    sentence = str(exc)
    return JSONResponse(status_code=409, content={"detail": {
        "code": "asset_corrupt", "message": sentence, "detail": sentence}})


def _version_taken(exc: AssetVersionTakenError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": {
        "code": "asset_version_taken", "message": str(exc)}})


def _io_error(what: str, exc: OSError) -> JSONResponse:
    """500 with a sentence for a file the library could not write or remove."""
    logger.error("graph library: could not %s: %s", what, exc)
    reason = exc.strerror or type(exc).__name__
    return JSONResponse(status_code=500, content={"detail": {
        "code": "library_io_error",
        "message": f"The library could not {what} ({reason}).  Nothing was changed; "
                   f"try again, and check the server's disk if it keeps failing."}})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_assets():
    return {"assets": await run_in_threadpool(asset_list)}


@router.get("/{name}/{version}")
async def get_asset(name: str, version: int):
    try:
        return await run_in_threadpool(get_library().get, name, version)
    except AssetNotFoundError:
        raise _not_found()
    except AssetCorruptError as exc:
        return _corrupt(exc)


@router.post("", status_code=201)
async def create_asset(request: Request):
    body = await _read_body(request)
    interface = body.interface.model_dump(by_alias=True) if body.interface is not None else None
    palette = body.palette.model_dump() if body.palette is not None else None
    network = body.network.model_dump()
    try:
        data = await run_in_threadpool(
            get_library().create,
            body.name,
            network,
            body.promoted,
            body.description,
            interface,
            palette,
        )
    except InvalidAssetError as exc:
        return _invalid(exc)
    except AssetVersionTakenError as exc:
        return _version_taken(exc)
    except _PARSE_ERRORS as exc:
        # The network does not parse: the error itself, no full graph check
        # (an asset network alone has no terminals, so that check would
        # only add noise).
        return JSONResponse(status_code=400, content=error_body(exc, []))
    except OSError as exc:
        return _io_error(f"save asset {body.name}", exc)
    return JSONResponse(status_code=201, content=data)


@router.delete("/{name}/{version}", status_code=204)
async def delete_asset(name: str, version: int):
    try:
        await run_in_threadpool(get_library().delete, name, version)
    except AssetNotFoundError:
        raise _not_found()
    except OSError as exc:
        return _io_error(f"delete asset {name} version {version}", exc)
    return Response(status_code=204)
