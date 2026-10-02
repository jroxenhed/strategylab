"""Write the node builder catalog out as TypeScript for the frontend.

The backend node registry is the one source of truth (plan decision D9):
every module in backend/nodebuilder/trading/ registers its node types, and
backend/nodebuilder/nodes.py lists them in catalog order.  This script turns
that into frontend/src/features/nodebuilder/catalog.generated.ts, which
catalog.ts reads.  A new node module needs no edit here.  backend/tests/nodebuilder/test_catalog_consistency.py renders the
file again in memory and fails when the committed copy is stale.

Usage (from the repo root):
    backend/venv/bin/python backend/scripts/export_nodebuilder_catalog.py
    backend/venv/bin/python backend/scripts/export_nodebuilder_catalog.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
OUT_PATH = REPO_ROOT / "frontend" / "src" / "features" / "nodebuilder" / "catalog.generated.ts"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from nodebuilder import nodes  # noqa: E402

HEADER = """\
// GENERATED FILE. Do not edit by hand.
//
// Source: the backend node registry (backend/nodebuilder/trading/nodes_*.py,
// listed by backend/nodebuilder/nodes.py; plan decision D9).
// Regenerate from the repo root with:
//   backend/venv/bin/python backend/scripts/export_nodebuilder_catalog.py
// backend/tests/nodebuilder/test_catalog_consistency.py fails when this file
// no longer matches the backend catalog.
//
// UI-only extras (unit display text, port helpers) live in catalog.ts.
"""


def _json(value: Any, indent: int = 0) -> str:
    """Stable JSON text, keys in the order given, two-space indent.

    A list of plain values (strings, numbers) stays on one line, and so does
    a small object of plain values, so the file is short enough to review.
    """
    pad, inner = "  " * indent, "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        if all(not isinstance(v, (dict, list, tuple)) for v in value.values()):
            one_line = "{ " + ", ".join(
                f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in value.items()
            ) + " }"
            if len(pad) + len(one_line) <= 100:
                return one_line
        items = [f"{inner}{json.dumps(k)}: {_json(v, indent + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        if all(not isinstance(v, (dict, list, tuple)) for v in value):
            return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
        items = [f"{inner}{_json(v, indent + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    return json.dumps(value, ensure_ascii=False)


def _union(values: tuple[str, ...]) -> str:
    return " | ".join(json.dumps(v) for v in values)


def render_catalog_ts() -> str:
    """Return the full text of catalog.generated.ts."""
    parts = [
        HEADER,
        f"export type ParamType = {_union(nodes.PARAM_TYPES)};",
        f"export type ParamUnit = {_union(nodes.PARAM_UNITS)};",
        f"export type ParamDtype = {_union(nodes.PARAM_DTYPES)};",
        "",
        "/** One param of a node type (plan section 4.3). */",
        "export interface ParamSpec {",
        "  name: string;",
        "  type: ParamType;",
        "  label: string;",
        "  default: unknown;",
        "  min?: number;",
        "  max?: number;",
        "  step?: number;",
        "  unit?: ParamUnit | null;",
        "  options?: readonly string[];",
        "  dtype?: ParamDtype;",
        "  optional?: boolean;",
        "  /** False for params that may never hold code (Ticker symbol and interval). */",
        "  code_able?: boolean;",
        "}",
        "",
        "/** One input port. Its id is in<k> by position. */",
        "export interface PortSpec {",
        "  label: string;",
        "  optional?: boolean;",
        "}",
        "",
        "/** The input ports of a node type (plan section 4.3). */",
        "export interface PortsSpec {",
        "  ports: readonly PortSpec[];",
        "  dynamic: boolean;",
        "  min: number;",
        "  max: number;",
        "}",
        "",
        "/** A backend catalog entry, as NodeCatalogEntry.to_json() gives it. */",
        "export interface GeneratedCatalogEntry {",
        "  name: string;",
        "  cat: string;",
        "  desc: string;",
        "  compile_active: boolean;",
        "  inputs: PortsSpec;",
        "  params: readonly ParamSpec[];",
        "  reads: readonly string[];",
        "  writes: readonly string[];",
        "  subtitle: string | null;",
        "  setting_key: string | null;",
        "  ins: number;",
        "  outs: number;",
        "}",
        "",
        f"export const INTERVAL_OPTIONS = {_json(list(nodes.INTERVAL_OPTIONS))} as const;",
        f"export const SOURCE_OPTIONS = {_json(list(nodes.SOURCE_OPTIONS))} as const;",
        f"export const RSI_TYPE_OPTIONS = {_json(list(nodes.RSI_TYPE_OPTIONS))} as const;",
        f"export const RSI_DEFAULT_TYPE = {json.dumps(nodes.RSI_DEFAULT_TYPE)};",
        f"export const TRAILING_STOP_TYPE_OPTIONS = {_json(list(nodes.TRAILING_STOP_TYPE_OPTIONS))} as const;",
        f"export const TRAILING_STOP_SOURCE_OPTIONS = {_json(list(nodes.TRAILING_STOP_SOURCE_OPTIONS))} as const;",
        f"export const TRAILING_STOP_DEFAULTS = {_json(nodes.TRAILING_STOP_DEFAULTS)} as const;",
        "",
        f"export const GENERATED_CATALOG: readonly GeneratedCatalogEntry[] = {_json(nodes.catalog_json())};",
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when the committed file is stale; write nothing")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="output path")
    args = parser.parse_args(argv)

    text = render_catalog_ts()
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if current != text:
            print(f"{args.out} is stale. Run this script without --check.", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date.")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
