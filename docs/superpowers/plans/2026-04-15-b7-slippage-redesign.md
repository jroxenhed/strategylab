# B7 — Slippage Model Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate *measured* slippage (diagnostic, signed bias allowed) from *modeled* slippage (backtest assumption, always ≥ 0, floored at default), owned by one shared module (`backend/slippage.py`) that every caller routes through. Units become basis points everywhere. Fixes journal sign bugs, bot-runner log sign drift, and the favorable-empirical auto-carry pitfall.

**Architecture:**
- One shared module `backend/slippage.py` owns sign + unit convention, policy, and aggregation. Two helpers (`slippage_cost_bps`, `fill_bias_bps`) + one policy fn (`decide_modeled_bps`) + three tunable constants.
- Journal rows gain a cached unsigned `slippage_bps` at write time; legacy rows compute lazily on read. No migration script.
- `StrategyRequest.slippage_pct` → `slippage_bps` (`ge=0.0`). `BotState.slippage_pcts` → `slippage_bps`, migrated lazily on `bots.json` deserialize. Frontend `localStorage` migrates saved strategies on read with `Math.max(0, pct * 100)`.
- `/api/slippage/{symbol}` returns `{modeled_bps, measured_bps, fill_bias_bps, fill_count, source}`; `StrategyBuilder` consumes only `modeled_bps`; `TradeJournal` renders unsigned per-row + signed bias in the header.

**Tech Stack:** Python 3 / FastAPI / Pydantic v2 / pytest (backend); React + TypeScript + Vite + TanStack Query (frontend).

**Session plan (6 sessions, each independently executable & commit-bounded):**

| Session | Scope | Deliverable |
|---|---|---|
| 1 | `backend/slippage.py` + `test_slippage.py` | Pure module, fully tested, no callers yet. |
| 2 | Journal write-time caching + `/api/slippage/{symbol}` rewrite | Endpoint returns new shape; new rows cache `slippage_bps`. |
| 3 | Backtester wiring (`StrategyRequest`, `routes/backtest.py`) | Backend accepts only `slippage_bps`; drag math unsigned-fed. |
| 4 | Bot runner (inline math → shared helpers, state field rename, lazy bots.json migration) | Live path coherent with journal + backtester. |
| 5 | Frontend (`useSlippage` hook, `StrategyBuilder`, `TradeJournal`, `BotCard`) | UI speaks bps end-to-end; journal signs fixed. |
| 6 | Manual verification pass (4-step checklist from spec §Rollout) | Sign-off. Mark B7 done in TODO.md. |

**Commit boundaries match session boundaries** so a future `git bisect` on any regression lands on the correct layer.

**Shared references (read once per session as needed):**
- Spec: `docs/superpowers/specs/2026-04-15-slippage-redesign-design.md`
- Convention table: spec §Core convention (side-inversion table).
- Policy pseudo-code: spec §Policy (`decide_modeled_bps`).
- Rollout ordering: spec §Rollout.

---

## Session 1 — `backend/slippage.py` module + tests

**Context prime (read only these):**
- Spec §Core convention, §Policy, §Testing (first two subsections).
- `backend/journal.py` — note: `JOURNAL_PATH` constant; journal file on disk is `{"trades": [...]}`, **not** a bare list. `_recent_fills` must read `.get("trades", [])`.

**Files:**
- Create: `backend/slippage.py`
- Create: `backend/tests/test_slippage.py`

**Scope:** Pure module. No other site imports it yet. TDD.

- [ ] **Step 1.1: Write the failing tests first**

Create `backend/tests/test_slippage.py` with the full helper + policy test matrix:

```python
"""Tests for backend/slippage.py — sign convention, policy, window behavior."""
import json
import pytest
from pathlib import Path

from slippage import (
    slippage_cost_bps,
    fill_bias_bps,
    decide_modeled_bps,
    ModeledSlippage,
    SLIPPAGE_DEFAULT_BPS,
    SLIPPAGE_MIN_FILLS,
    SLIPPAGE_WINDOW,
)


# ---------- slippage_cost_bps: side-inversion table ----------

@pytest.mark.parametrize("side,expected,fill,want", [
    # side,    expected, fill,   cost_bps
    ("buy",    100.0,    100.10, 10.0),   # worse-above
    ("buy",    100.0,     99.90,  0.0),   # favorable clamps to 0
    ("cover",  100.0,    100.10, 10.0),
    ("cover",  100.0,     99.90,  0.0),
    ("sell",   100.0,     99.90, 10.0),   # worse-below
    ("sell",   100.0,    100.10,  0.0),
    ("short",  100.0,     99.90, 10.0),
    ("short",  100.0,    100.10,  0.0),
])
def test_cost_bps_side_table(side, expected, fill, want):
    assert slippage_cost_bps(side, expected, fill) == pytest.approx(want, abs=1e-6)


def test_cost_bps_case_insensitive():
    assert slippage_cost_bps("BUY", 100.0, 100.10) == pytest.approx(10.0)
    assert slippage_cost_bps("Sell", 100.0, 99.90) == pytest.approx(10.0)


def test_cost_bps_zero_expected_is_zero():
    # Defensive: division-by-zero guard returns 0 rather than raising.
    assert slippage_cost_bps("buy", 0.0, 1.0) == 0.0


# ---------- fill_bias_bps: signed, positive = favorable ----------

def test_bias_buy_favorable_is_positive():
    assert fill_bias_bps("buy", 100.0, 99.90) == pytest.approx(10.0)


def test_bias_buy_unfavorable_is_negative():
    assert fill_bias_bps("buy", 100.0, 100.10) == pytest.approx(-10.0)


def test_bias_sell_favorable_is_positive():
    assert fill_bias_bps("sell", 100.0, 100.10) == pytest.approx(10.0)


def test_bias_sell_unfavorable_is_negative():
    assert fill_bias_bps("sell", 100.0, 99.90) == pytest.approx(-10.0)


def test_bias_symmetry_with_cost():
    # Unfavorable: bias = -cost. Favorable: bias > 0, cost = 0.
    for side in ("buy", "sell", "cover", "short"):
        bad_exp, bad_fill = (100.0, 100.10) if side in ("buy", "cover") else (100.0, 99.90)
        good_exp, good_fill = (100.0, 99.90) if side in ("buy", "cover") else (100.0, 100.10)
        assert fill_bias_bps(side, bad_exp, bad_fill) == pytest.approx(
            -slippage_cost_bps(side, bad_exp, bad_fill)
        )
        assert fill_bias_bps(side, good_exp, good_fill) > 0
        assert slippage_cost_bps(side, good_exp, good_fill) == 0.0


# ---------- decide_modeled_bps: policy ----------

@pytest.fixture
def fake_journal(tmp_path, monkeypatch):
    """Point both journal module and slippage module at an empty temp journal.

    The on-disk shape is {"trades": [...]} — see backend/journal.py._log_trade.
    """
    f = tmp_path / "trade_journal.json"
    f.write_text('{"trades": []}')
    import journal
    import slippage as slip_mod
    monkeypatch.setattr(journal, "JOURNAL_PATH", f)
    monkeypatch.setattr(slip_mod, "JOURNAL_PATH", f)
    return f


def _write_fills(f: Path, fills: list[dict]):
    f.write_text(json.dumps({"trades": fills}))


def _fill(symbol: str, side: str, expected: float, price: float):
    return {"symbol": symbol, "side": side, "expected_price": expected, "price": price}


def test_policy_empty_journal(fake_journal):
    result = decide_modeled_bps("AAPL")
    assert result == ModeledSlippage(
        modeled_bps=SLIPPAGE_DEFAULT_BPS,
        measured_bps=None,
        fill_bias_bps=None,
        fill_count=0,
        source="default",
    )


def test_policy_below_min_fills_uses_default(fake_journal):
    # 5 unfavorable fills: measured is real but fill_count < MIN
    fills = [_fill("AAPL", "buy", 100.0, 100.05) for _ in range(5)]
    _write_fills(fake_journal, fills)

    r = decide_modeled_bps("AAPL")
    assert r.modeled_bps == SLIPPAGE_DEFAULT_BPS
    assert r.source == "default"
    assert r.measured_bps == pytest.approx(5.0)
    assert r.fill_count == 5


def test_policy_above_min_favorable_floors_at_default(fake_journal):
    # 25 favorable fills: measured = 0, but modeled floors at default
    fills = [_fill("AAPL", "buy", 100.0, 99.95) for _ in range(25)]
    _write_fills(fake_journal, fills)

    r = decide_modeled_bps("AAPL")
    assert r.modeled_bps == SLIPPAGE_DEFAULT_BPS
    assert r.source == "empirical"
    assert r.measured_bps == pytest.approx(0.0)
    assert r.fill_bias_bps == pytest.approx(5.0)  # favorable by 5 bps


def test_policy_above_min_unfavorable_uses_measured(fake_journal):
    # 25 fills at 3 bps cost → modeled = 3.0 (> default 2.0)
    fills = [_fill("AAPL", "buy", 100.0, 100.03) for _ in range(25)]
    _write_fills(fake_journal, fills)

    r = decide_modeled_bps("AAPL")
    assert r.modeled_bps == pytest.approx(3.0)
    assert r.source == "empirical"


def test_policy_window_cap(fake_journal):
    # 100 fills total: 60 old bad (10 bps) + 40 recent good (0 bps favorable).
    # Window of 50 = last 50 = 10 bad + 40 good → measured = (10*10 + 40*0)/50 = 2.0
    old = [_fill("AAPL", "buy", 100.0, 100.10) for _ in range(60)]
    new = [_fill("AAPL", "buy", 100.0,  99.90) for _ in range(40)]
    _write_fills(fake_journal, old + new)

    r = decide_modeled_bps("AAPL")
    assert r.fill_count == SLIPPAGE_WINDOW  # 50
    assert r.measured_bps == pytest.approx(2.0)


def test_policy_symbol_case_insensitive(fake_journal):
    fills = [_fill("AAPL", "buy", 100.0, 100.03) for _ in range(25)]
    _write_fills(fake_journal, fills)
    assert decide_modeled_bps("aapl").fill_count == 25


def test_policy_ignores_other_symbols(fake_journal):
    fills = [_fill("TSLA", "buy", 100.0, 100.10) for _ in range(30)]
    _write_fills(fake_journal, fills)
    r = decide_modeled_bps("AAPL")
    assert r.fill_count == 0
    assert r.source == "default"


def test_policy_skips_rows_missing_expected_price(fake_journal):
    # Legacy rows without expected_price must not crash or contribute.
    fills = [
        {"symbol": "AAPL", "side": "buy", "price": 100.10},  # no expected_price
        _fill("AAPL", "buy", 100.0, 100.03),
    ]
    _write_fills(fake_journal, fills)
    r = decide_modeled_bps("AAPL")
    assert r.fill_count == 1
    assert r.measured_bps == pytest.approx(3.0)
```

- [ ] **Step 1.2: Run tests — confirm they fail with ImportError**

```bash
cd backend && python -m pytest tests/test_slippage.py -v
```
Expected: collection error / `ModuleNotFoundError: No module named 'slippage'`.

- [ ] **Step 1.3: Write the module**

Create `backend/slippage.py`:

```python
"""Slippage cost/bias helpers + modeled-value policy.

One module owns the sign and unit convention. Every caller (journal writer,
/api/slippage endpoint, bot runner, backtester) routes through these helpers.
Units: basis points everywhere (1 bp = 0.01%).

Conventions:
- slippage_cost_bps: unsigned cost to the trader, always >= 0. Favorable → 0.
- fill_bias_bps: signed deviation, positive = favorable. Diagnostic only.

Side inversion:
- buy / cover: worse when fill > expected.
- sell / short: worse when fill < expected.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Literal

# Tunable policy constants.
SLIPPAGE_DEFAULT_BPS: float = 2.0
SLIPPAGE_MIN_FILLS:   int   = 20
SLIPPAGE_WINDOW:      int   = 50

from journal import JOURNAL_PATH  # journal file lives at backend/data/trade_journal.json

_WORSE_WHEN_ABOVE = {"buy", "cover"}
_WORSE_WHEN_BELOW = {"sell", "short"}


def _raw_bps(side: str, expected: float, fill: float) -> float | None:
    """Signed raw bps where positive = unfavorable (cost).
    Returns None if side unknown or expected is zero/negative."""
    if expected <= 0:
        return None
    s = side.lower()
    if s in _WORSE_WHEN_ABOVE:
        return (fill - expected) / expected * 1e4
    if s in _WORSE_WHEN_BELOW:
        return (expected - fill) / expected * 1e4
    return None


def slippage_cost_bps(side: str, expected: float, fill: float) -> float:
    """Unsigned cost in bps. Always >= 0. Favorable fills return 0."""
    raw = _raw_bps(side, expected, fill)
    if raw is None:
        return 0.0
    return max(0.0, raw)


def fill_bias_bps(side: str, expected: float, fill: float) -> float:
    """Signed deviation in bps. Positive = favorable, negative = unfavorable."""
    raw = _raw_bps(side, expected, fill)
    if raw is None:
        return 0.0
    return -raw  # invert so positive = favorable


@dataclass(frozen=True)
class Fill:
    side: str
    expected: float
    fill: float


@dataclass(frozen=True)
class ModeledSlippage:
    modeled_bps:    float
    measured_bps:   float | None
    fill_bias_bps:  float | None
    fill_count:     int
    source:         Literal["default", "empirical"]


def _recent_fills(symbol: str, limit: int) -> list[Fill]:
    """Read the trade journal and return up to `limit` most-recent fills for
    `symbol` that have both expected_price and price. Newest last."""
    if not JOURNAL_PATH.exists():
        return []
    try:
        rows = json.loads(JOURNAL_PATH.read_text() or '{"trades":[]}').get("trades", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    sym = symbol.upper()
    out: list[Fill] = []
    for row in rows:
        if (row.get("symbol") or "").upper() != sym:
            continue
        exp = row.get("expected_price")
        px  = row.get("price")
        side = row.get("side")
        if exp is None or px is None or side is None:
            continue
        out.append(Fill(side=side, expected=float(exp), fill=float(px)))
    return out[-limit:]


def decide_modeled_bps(symbol: str) -> ModeledSlippage:
    """Return the modeled cost the backtester should use for `symbol`,
    plus the diagnostic aggregates over the same window.

    Policy: empirical can make the model WORSE, never better than default."""
    fills = _recent_fills(symbol, limit=SLIPPAGE_WINDOW)
    n = len(fills)

    if n == 0:
        return ModeledSlippage(
            modeled_bps=SLIPPAGE_DEFAULT_BPS,
            measured_bps=None,
            fill_bias_bps=None,
            fill_count=0,
            source="default",
        )

    measured = mean(slippage_cost_bps(f.side, f.expected, f.fill) for f in fills)
    bias     = mean(fill_bias_bps(f.side, f.expected, f.fill)    for f in fills)

    if n < SLIPPAGE_MIN_FILLS:
        return ModeledSlippage(
            modeled_bps=SLIPPAGE_DEFAULT_BPS,
            measured_bps=measured,
            fill_bias_bps=bias,
            fill_count=n,
            source="default",
        )

    return ModeledSlippage(
        modeled_bps=max(SLIPPAGE_DEFAULT_BPS, measured),
        measured_bps=measured,
        fill_bias_bps=bias,
        fill_count=n,
        source="empirical",
    )
```

- [ ] **Step 1.4: Run tests — confirm all pass**

```bash
cd backend && python -m pytest tests/test_slippage.py -v
```
Expected: all tests pass (20+ test cases, no failures, no warnings).

- [ ] **Step 1.5: Commit**

```bash
git add backend/slippage.py backend/tests/test_slippage.py
git commit -m "feat(slippage): add shared cost/bias helpers + policy (B7 session 1)"
```

**Session 1 done.** Nothing else imports `slippage.py` yet; next session wires the journal writer and endpoint.

---

## Session 2 — Journal write-time caching + `/api/slippage/{symbol}` rewrite

**Context prime:**
- Spec §Data model changes → "Journal row (hybrid storage)"; spec §API.
- `backend/journal.py:88-118` (`_log_trade` signature + row shape).
- `backend/routes/slippage.py` (entire file — 54 lines, it's getting rewritten).
- `backend/tests/test_slippage_endpoint.py` (entire file — rewritten).

**Scope:** Teach `_log_trade` to cache unsigned `slippage_bps` on the row. Rewrite the endpoint to return the five-field shape via `decide_modeled_bps`. Nothing else changes — bot runner / backtester / frontend still speak the old `slippage_pct` contract; we fix them in sessions 3–5.

**Files:**
- Modify: `backend/journal.py` (`_log_trade` — add `slippage_bps` to the written row)
- Modify: `backend/routes/slippage.py` (full rewrite, ~25 lines)
- Modify: `backend/tests/test_slippage_endpoint.py` (full rewrite against new shape)

---

- [ ] **Step 2.1: Update `_log_trade` to cache `slippage_bps`**

In `backend/journal.py`, at the top of the file add the helper import:

```python
from slippage import slippage_cost_bps
```

Then modify the row append in `_log_trade` (around line 103):

```python
    cost_bps: float | None = None
    if price is not None and expected_price is not None and side is not None:
        cost_bps = round(slippage_cost_bps(side, expected_price, price), 2)

    journal["trades"].append({
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "stop_loss_price": stop_loss_price,
        "source": source,
        "reason": reason,
        "expected_price": expected_price,
        "slippage_bps": cost_bps,   # None when we can't compute (e.g. missing fill)
        "direction": direction,
        "bot_id": bot_id,
        "broker": broker,
    })
```

**Why `None` instead of 0 when we can't compute:** a zero would be indistinguishable from "favorable fill, clamped to 0". `None` means "no data"; downstream readers fall back to the lazy-derive path.

- [ ] **Step 2.2: Rewrite `backend/routes/slippage.py`**

Replace the entire file with:

```python
"""GET /api/slippage/{symbol} — modeled + measured slippage diagnostics.

All sign/unit convention lives in backend/slippage.py. This route only shapes
the response for the frontend.
"""
from fastapi import APIRouter

from slippage import decide_modeled_bps

router = APIRouter()


@router.get("/api/slippage/{symbol}")
def get_slippage(symbol: str):
    r = decide_modeled_bps(symbol)
    return {
        "modeled_bps":    round(r.modeled_bps, 2),
        "measured_bps":   None if r.measured_bps  is None else round(r.measured_bps,  2),
        "fill_bias_bps":  None if r.fill_bias_bps is None else round(r.fill_bias_bps, 2),
        "fill_count":     r.fill_count,
        "source":         r.source,
    }
```

The `_signed_slippage_pct` helper and the `_WORSE_IF_FILL_IS` map are deleted — they were exactly the broken signed-pct convention we're retiring.

- [ ] **Step 2.3: Rewrite `backend/tests/test_slippage_endpoint.py`**

Replace the entire file with:

```python
"""Tests for GET /api/slippage/{symbol} — new shape."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import pytest
from fastapi.testclient import TestClient

from main import app
import journal
import slippage as slip_mod


@pytest.fixture
def fake_journal(tmp_path, monkeypatch):
    """Point journal + slippage module at a temp file. Shape: {"trades": [...]}."""
    path = tmp_path / "trade_journal.json"
    monkeypatch.setattr(journal, "JOURNAL_PATH", path)
    monkeypatch.setattr(slip_mod, "JOURNAL_PATH", path)

    def write(trades):
        path.write_text(json.dumps({"trades": trades}, indent=2))

    return write


def _fill(symbol="AAPL", side="buy", expected=100.0, price=100.0):
    return {"symbol": symbol, "side": side, "price": price, "expected_price": expected}


def test_empty_journal_returns_default(fake_journal):
    fake_journal([])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body == {
        "modeled_bps":    2.0,
        "measured_bps":   None,
        "fill_bias_bps":  None,
        "fill_count":     0,
        "source":         "default",
    }


def test_below_min_fills_source_is_default(fake_journal):
    # 5 unfavorable buy fills at 5 bps cost
    fake_journal([_fill(side="buy", expected=100.0, price=100.05) for _ in range(5)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["source"] == "default"
    assert body["modeled_bps"] == 2.0
    assert body["measured_bps"] == pytest.approx(5.0)
    assert body["fill_count"] == 5


def test_above_min_favorable_floors_at_default(fake_journal):
    fake_journal([_fill(side="buy", expected=100.0, price=99.95) for _ in range(25)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["source"] == "empirical"
    assert body["modeled_bps"] == 2.0          # floored — empirical didn't lower it
    assert body["measured_bps"] == pytest.approx(0.0)
    assert body["fill_bias_bps"] == pytest.approx(5.0)   # +5 bps favorable


def test_above_min_unfavorable_uses_measured(fake_journal):
    fake_journal([_fill(side="buy", expected=100.0, price=100.03) for _ in range(25)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["source"] == "empirical"
    assert body["modeled_bps"] == pytest.approx(3.0)
    assert body["fill_bias_bps"] == pytest.approx(-3.0)  # unfavorable


def test_sell_side_sign_convention(fake_journal):
    # sell filled BELOW expected → unfavorable. cost should be positive.
    fake_journal([_fill(side="sell", expected=100.0, price=99.90) for _ in range(25)])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["measured_bps"] == pytest.approx(10.0)
    assert body["fill_bias_bps"] == pytest.approx(-10.0)


def test_symbol_case_insensitive(fake_journal):
    fake_journal([_fill(symbol="AAPL", side="buy", expected=100.0, price=100.05)])
    body = TestClient(app).get("/api/slippage/aapl").json()
    assert body["fill_count"] == 1


def test_skips_rows_missing_expected_price(fake_journal):
    fake_journal([
        {"symbol": "AAPL", "side": "buy", "price": 100.0, "expected_price": None},
        _fill(side="buy", expected=100.0, price=100.03),
    ])
    body = TestClient(app).get("/api/slippage/AAPL").json()
    assert body["fill_count"] == 1
```

- [ ] **Step 2.4: Add a write-path caching test to `test_slippage.py`**

Append this to `backend/tests/test_slippage.py` (reuses the existing `fake_journal` fixture):

```python
def test_log_trade_caches_slippage_bps_on_row(fake_journal):
    import journal
    journal._log_trade(
        symbol="AAPL", side="buy", qty=10, price=100.10,
        source="bot", expected_price=100.0, direction="long", bot_id="b1",
    )
    rows = json.loads(fake_journal.read_text())["trades"]
    assert rows[-1]["slippage_bps"] == pytest.approx(10.0)


def test_log_trade_caches_zero_on_favorable_fill(fake_journal):
    import journal
    journal._log_trade(
        symbol="AAPL", side="buy", qty=10, price=99.90,
        source="bot", expected_price=100.0, direction="long", bot_id="b1",
    )
    rows = json.loads(fake_journal.read_text())["trades"]
    assert rows[-1]["slippage_bps"] == 0.0


def test_log_trade_caches_none_when_no_expected(fake_journal):
    import journal
    journal._log_trade(
        symbol="AAPL", side="buy", qty=10, price=100.10,
        source="bot", expected_price=None, direction="long", bot_id="b1",
    )
    rows = json.loads(fake_journal.read_text())["trades"]
    assert rows[-1]["slippage_bps"] is None
```

**Note:** the `fake_journal` fixture in `test_slippage.py` now also needs to monkeypatch `journal.JOURNAL_PATH` — already done in the session 1 fix. If you land session 1 without that fixture change, these tests will write to the real journal. Double-check by opening `test_slippage.py` and verifying the fixture body matches the session 1 code.

- [ ] **Step 2.5: Run all slippage tests**

```bash
cd backend && python -m pytest tests/test_slippage.py tests/test_slippage_endpoint.py -v
```
Expected: all tests pass (session 1 tests + 3 new write-path tests + 7 endpoint tests).

- [ ] **Step 2.6: Sanity-check against real journal**

```bash
cd backend && python -c "from slippage import decide_modeled_bps; print(decide_modeled_bps('AAPL'))"
```
Expected: a `ModeledSlippage(...)` line, no traceback. If the real journal has AAPL fills, `source` may be `"empirical"` or `"default"` depending on count — both are fine. Goal is "reads the real file without crashing".

- [ ] **Step 2.7: Commit**

```bash
git add backend/journal.py backend/routes/slippage.py backend/tests/test_slippage_endpoint.py backend/tests/test_slippage.py
git commit -m "feat(slippage): cache slippage_bps on journal rows, rewrite /api/slippage (B7 session 2)"
```

**Session 2 done.** Journal writes now cache the unsigned cost; the endpoint returns the new five-field shape. Backtester, bot runner, and frontend still use the old contract — next three sessions migrate them in rollout order.

---

## Session 3 — Backtester wiring (`StrategyRequest` + `routes/backtest.py`)

**Context prime:**
- Spec §Backtester (the whole section — it's short).
- `backend/models.py:49` (`StrategyRequest.slippage_pct` field).
- `backend/routes/backtest.py:186-191` (entry drag) and `:262-266` (exit drag).
- `backend/tests/test_backtest_short.py:92-103` (the only test that references `slippage_pct`).

**Scope:** Rename the `StrategyRequest` field, add `ge=0.0` validation, convert drag math from `pct/100` to `bps/10_000`. Update the one test that passes the field. Backend-only — the frontend still sends `slippage_pct`, which will 422 after this session; that's fixed in session 5. **This session will briefly leave the frontend broken** at the commit boundary — acceptable because single-user / single-deploy and sessions 3–5 all ship together in one PR per spec §Rollout.

**Files:**
- Modify: `backend/models.py` (rename field, add validator)
- Modify: `backend/routes/backtest.py` (drag math — 4 lines total)
- Modify: `backend/tests/test_backtest_short.py` (rename + rescale fixture)
- Modify: `backend/bot_manager.py:251` (one line — see step 3.2b below; prevents a silent regression in `backtest_bot`)

---

- [ ] **Step 3.1: Rename the `StrategyRequest` field**

In `backend/models.py`, replace line 49:

```python
    slippage_pct: float = 0.0    # e.g. 0.1 means 0.1% worse fill on every trade
```

With:

```python
    slippage_bps: float = Field(default=2.0, ge=0.0)   # unsigned cost per leg, bps
```

Ensure `Field` is imported at the top of the file:

```python
from pydantic import BaseModel, Field
```

(If `Field` is already imported, leave the import line alone.)

**Why default `2.0`, not `0.0`:** the spec decision. `SLIPPAGE_DEFAULT_BPS` is 2 bps; a backtest that forgets to specify should assume the default, not a friction-free market. This is a deliberate behavior change — see Step 3.4 for existing-test impact analysis.

- [ ] **Step 3.2: Update drag math in `routes/backtest.py`**

At the top of `run_backtest` (or near the top of the function body where per-request constants are computed), introduce a fractional drag once:

```python
    drag = req.slippage_bps / 10_000.0   # bps → fractional
```

Then replace the entry block at lines 186-191. The comment line above it stays; only the four lines of math change:

```python
                # Slippage: short entry fills lower (worse for seller), long fills higher (worse for buyer)
                if is_short:
                    fill_price = price * (1 - drag)
                else:
                    fill_price = price * (1 + drag)
```

And the exit block at lines 262-266:

```python
                # Slippage: short covers at higher price (worse), long sells at lower price (worse)
                if is_short:
                    exit_price = raw_exit * (1 + drag)
                else:
                    exit_price = raw_exit * (1 - drag)
```

The `abs(...)` wrappers on `entry_slippage` and `exit_slippage` (lines 199 and 269) stay — they convert the signed price delta to a positive dollar amount, which is orthogonal to the bps rename.

**Grep check before saving:** `rg 'slippage_pct' backend/routes/backtest.py` must return zero hits after this edit.

- [ ] **Step 3.2b: Bridge the `backtest_bot` call site**

In `backend/bot_manager.py:251`, replace:

```python
            slippage_pct=config.slippage_pct,
```

With:

```python
            slippage_bps=config.slippage_pct * 100,   # TEMP bridge — session 4 renames config field
```

**Why this is needed:** `BotConfig.slippage_pct` is still percent (session 4 renames it). Without this bridge, `StrategyRequest(..., slippage_pct=...)` becomes an extra field that Pydantic silently drops (default `extra="ignore"`), and every bot backtest between session 3 and session 4 commits would use the 2 bps default regardless of the bot's configured value. One line now prevents a silent regression.

Session 4 step 4.6 replaces `config.slippage_pct * 100` with `config.slippage_bps` and removes the comment.

- [ ] **Step 3.3: Update `test_backtest_short.py` fixture**

In `backend/tests/test_backtest_short.py:94`, replace:

```python
    req = _req_short(slippage_pct=1.0)
```

With:

```python
    req = _req_short(slippage_bps=100.0)   # 100 bps = 1% — preserves original test magnitude
```

The original test used `slippage_pct=1.0` (= 1% drag) to make the fill-direction assertions at lines 100 and 103 visibly diverge from the raw price. 100 bps keeps the same magnitude, so the assertions (`entry fill < 100`, `cover fill > 90`) stay meaningful.

- [ ] **Step 3.4: Audit other backtest tests for the new default**

The default changed from `0.0` → `2.0`. Any test that doesn't specify slippage will now get 2 bps of drag. Walk each test in `test_backtest_short.py`:

| Test | Effect of 2 bps drag | Assertion stability |
|---|---|---|
| `test_short_trade_types_are_short_and_cover` | Entry 99.98, cover 90.018 | Types unchanged — PASS |
| `test_short_pnl_positive_when_price_drops` | PnL ≈ (99.98 - 90.018) * shares | Still positive — PASS |
| `test_short_pnl_negative_when_price_rises` | PnL ≈ (99.98 - 110.022) * shares | Still negative — PASS |
| `test_short_stop_loss_triggers_above_entry` | Entry 99.98, stop = 99.98 * 1.03 = 102.98; bar high 104.03 >= 102.98 | Still triggers — PASS |
| `test_long_still_works` | Buy 100.02, sell 109.978, PnL positive | PASS |

No test changes needed beyond 3.3. Run the full file to confirm.

If any of these now fail, the root cause is the default change — **do not** re-introduce a 0.0 default to paper over a failure. Either (a) the math above is wrong and the test needs a different fix, or (b) the test was implicitly relying on zero drag and should declare `slippage_bps=0.0` explicitly.

- [ ] **Step 3.5: Run all backend tests**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: every test passes. Specifically:
- `tests/test_slippage.py` — unchanged from session 1+2 (still passes).
- `tests/test_slippage_endpoint.py` — unchanged from session 2 (still passes).
- `tests/test_backtest_short.py` — 7 tests, all green.
- Any other backtest tests (if present) — green.

If a non-slippage test fails, stop and investigate. Do not proceed.

- [ ] **Step 3.6: Grep sweep — backend should no longer reference `slippage_pct`**

```bash
cd backend && rg 'slippage_pct' --type py
```

Expected remaining hits (all **acceptable** at this commit boundary; each is fixed in a later session):
- `backend/bot_manager.py` — `BotConfig.slippage_pct`, `state.slippage_pcts`, manual-buy inline math, `avg_slippage_pct` summary. (Line 251 now uses `slippage_bps=config.slippage_pct * 100` — bridge removed in session 4.) Fixed in session 4.
- `backend/bot_runner.py:297-302` and `:461-466` — inline math. Fixed in session 4.
- `backend/data/bots.json` — persisted state. Migrated lazily by session 4 code.

Zero remaining hits **expected** in:
- `backend/models.py`
- `backend/routes/backtest.py`
- `backend/slippage.py` / `backend/routes/slippage.py`
- `backend/tests/` (except possibly `test_backtest_short.py` comments — strip those if you find any)

If `models.py` or `routes/backtest.py` still contains `slippage_pct`, you missed a spot — go back to 3.1 / 3.2.

- [ ] **Step 3.7: Commit**

```bash
git add backend/models.py backend/routes/backtest.py backend/bot_manager.py backend/tests/test_backtest_short.py
git commit -m "feat(backtest): rename slippage_pct → slippage_bps, add ge=0 validation (B7 session 3)"
```

**Session 3 done.** Backtester now speaks bps end-to-end on the server side. The frontend still sends `slippage_pct` in its POST body — the backend will 422 until session 5 lands. That's intentional: the full PR keeps backend and frontend coherent; we just can't keep every individual commit bisectable on the public API without deeper gymnastics the spec rejected ("no dual-field grace period").

**Bot runner still sets `state.slippage_pcts` and bot_manager still accepts `BotConfig.slippage_pct`** — those are internal to bot code and decoupled from `StrategyRequest`. Fixed next session.

---

## Session 4 — Bot runner + bot_manager (inline math → shared helpers, field rename, lazy migration)

**Context prime:**
- Spec §Bot runner; spec §Data model → "`BotState.slippage_pcts → slippage_bps`".
- `backend/bot_runner.py:296-302` (entry fill) and `:460-466` (exit fill). **The exit block at :461-463 has the latent sign bug the spec calls out** — both long and short branches compute `sell_fill - price`, which is positive on favorable long exits. Shared helper fixes this by construction.
- `backend/bot_manager.py:58` (`BotConfig.slippage_pct`), `:91, :116, :128` (`BotState.slippage_pcts` field + serde), `:251` (bridge from session 3), `:334-340, :349, :359` (manual-buy inline math — third site, spec-silent but must be unified), `:380` (`avg_slippage_pct` in `list_bots`), `:397-422` (`save` / `load`).

**Scope:** Three inline-math sites collapse to shared helpers. `state.slippage_pcts` → `state.slippage_bps`. `BotConfig.slippage_pct` → `BotConfig.slippage_bps` (unit change percent → bps, so stored values scale by 100). Lazy `bots.json` migration on load.

**Files:**
- Modify: `backend/bot_runner.py` (2 sites, ~14 lines each)
- Modify: `backend/bot_manager.py` (config field, state field, serde, manual-buy site, summary, bridge removal)
- No new tests in this session (bot runner/manager have no existing unit tests in this repo; integration via manual verification in session 6).

---

- [ ] **Step 4.1: Add shared-helper imports in `bot_runner.py`**

At the top of `backend/bot_runner.py`, alongside existing imports:

```python
from slippage import slippage_cost_bps, fill_bias_bps
```

- [ ] **Step 4.2: Replace entry-fill inline math in `bot_runner.py:296-302`**

Replace:

```python
                if is_short:
                    slippage = price - fill_price  # lower fill is worse for short seller
                else:
                    slippage = fill_price - price  # higher fill is worse for buyer
                slippage_pct = (slippage / price) * 100 if price else 0
                state.slippage_pcts.append(round(slippage_pct, 4))
                self._log("TRADE", f"{side_label} {qty} {cfg.symbol} @ {fill_price:.2f} (expected={price:.2f}, slippage={slippage_pct:+.4f}%)")
```

With:

```python
                side_key = "short" if is_short else "buy"
                cost_bps = slippage_cost_bps(side_key, expected=price, fill=fill_price)
                bias_bps = fill_bias_bps(side_key, expected=price, fill=fill_price)
                state.slippage_bps.append(round(cost_bps, 2))
                self._log(
                    "TRADE",
                    f"{side_label} {qty} {cfg.symbol} @ {fill_price:.2f} "
                    f"(expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
                )
```

`side_label` (uppercase `"BUY"` / `"SHORT"`) stays in the log for reader familiarity; `side_key` (lowercase) goes to the helpers. `slippage_cost_bps` lowercases internally but being explicit here means the helper's lookup table is keyed on values it actually holds — easier to audit.

- [ ] **Step 4.3: Replace exit-fill inline math in `bot_runner.py:460-466`**

Replace:

```python
                if is_short:
                    slippage = sell_fill - price  # higher cover fill is worse
                else:
                    slippage = sell_fill - price
                slippage_pct = (slippage / price) * 100 if price else 0
                state.slippage_pcts.append(round(slippage_pct, 4))
                self._log("TRADE", f"{exit_label} {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (expected={price:.2f}, slippage={slippage_pct:+.4f}%)")
```

With:

```python
                side_key = "cover" if is_short else "sell"
                cost_bps = slippage_cost_bps(side_key, expected=price, fill=sell_fill)
                bias_bps = fill_bias_bps(side_key, expected=price, fill=sell_fill)
                state.slippage_bps.append(round(cost_bps, 2))
                self._log(
                    "TRADE",
                    f"{exit_label} {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason={exit_reason} "
                    f"(expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
                )
```

**The latent sign bug at the old line 461-463 is gone.** Both branches (long sell, short cover) now route through `slippage_cost_bps` with the correct `side_key`, so long sells no longer report favorable fills as positive cost.

- [ ] **Step 4.4: Rename `BotState.slippage_pcts` → `BotState.slippage_bps`**

In `backend/bot_manager.py`, change the `BotState` dataclass field at line 91:

```python
    slippage_bps: list = field(default_factory=list)  # list of unsigned cost (bps) per fill
```

Update `BotState.to_dict` at line 116:

```python
            "slippage_bps": self.slippage_bps,
```

`BotState.from_dict` (lines 123-129) needs explicit migration handling — `setattr` would silently drop the legacy `slippage_pcts` key since the attribute no longer exists. Replace:

```python
    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        s = cls()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s
```

With:

```python
    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        s = cls()
        # Lazy migration: legacy bots.json has slippage_pcts (signed %); scale to bps (unsigned).
        # max(0, ...) retroactively applies the new "cost >= 0" rule to stored favorable values.
        if "slippage_pcts" in d and "slippage_bps" not in d:
            d = {**d, "slippage_bps": [max(0.0, v) * 100 for v in d["slippage_pcts"] or []]}
            d.pop("slippage_pcts", None)
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s
```

The migrated dict is a shallow copy so we don't mutate `data.get("bots", [])` from `load()` while we're iterating it. After the next `save()`, `slippage_pcts` is gone from disk; load is idempotent.

- [ ] **Step 4.5: Rename `BotConfig.slippage_pct` → `BotConfig.slippage_bps`**

At `backend/bot_manager.py:58`, replace:

```python
    slippage_pct: float = 0.0
```

With:

```python
    slippage_bps: float = Field(default=2.0, ge=0.0)
```

Ensure `Field` is imported from pydantic at the top of the file (grep — it may already be).

**Migration for `BotConfig` — Pydantic v2 behavior:** Pydantic with default `extra="ignore"` silently drops `slippage_pct` on load from old `bots.json` and uses the new `slippage_bps` default (2.0). That's **unacceptable here** — existing bots would jump from their configured 0.5% to the new 2 bps default, which could silently change live execution's backtest assumption.

Fix: in `BotManager.load()` (line 416), migrate each `config` dict before instantiating `BotConfig`:

```python
            for entry in data.get("bots", []):
                cfg_dict = entry["config"]
                # Lazy migration: old key 'slippage_pct' (percent) → 'slippage_bps' (bps).
                # max(0, ...) retroactively applies the "cost >= 0" rule.
                if "slippage_pct" in cfg_dict and "slippage_bps" not in cfg_dict:
                    cfg_dict = {**cfg_dict, "slippage_bps": max(0.0, cfg_dict["slippage_pct"]) * 100}
                    cfg_dict.pop("slippage_pct", None)
                config = BotConfig(**cfg_dict)
                state = BotState.from_dict(entry.get("state", {}))
                state.status = "stopped"
                self.bots[config.bot_id] = (config, state)
```

After the next `save()`, both old keys are gone from disk.

- [ ] **Step 4.6: Fix the `backtest_bot` bridge from session 3**

At `backend/bot_manager.py:251`, replace:

```python
            slippage_bps=config.slippage_pct * 100,   # TEMP bridge — session 4 renames config field
```

With:

```python
            slippage_bps=config.slippage_bps,
```

- [ ] **Step 4.7: Unify the manual-buy site**

At `backend/bot_manager.py:334-340, :349, :359` (inside `manual_buy`):

Replace:

```python
        # Update bot state
        if is_short:
            slippage = price - fill_price  # lower fill is worse for short seller
        else:
            slippage = fill_price - price  # higher fill is worse for buyer
        slippage_pct = (slippage / price) * 100 if price else 0
        state.slippage_pcts.append(round(slippage_pct, 4))
        state.entry_price = fill_price
        state.trail_peak = fill_price
        state.trades_count += 1
        side_label = "SHORT" if is_short else "BUY"
        state.last_signal = f"{side_label} (manual)"

        # Log
        runner = BotRunner(config, state, self)
        runner._log("TRADE", f"{side_label} {qty} {config.symbol} @ {fill_price:.2f} (manual, expected={price:.2f}, slippage={slippage_pct:+.4f}%)")
```

With:

```python
        # Update bot state
        side_key = "short" if is_short else "buy"
        cost_bps = slippage_cost_bps(side_key, expected=price, fill=fill_price)
        bias_bps = fill_bias_bps(side_key, expected=price, fill=fill_price)
        state.slippage_bps.append(round(cost_bps, 2))
        state.entry_price = fill_price
        state.trail_peak = fill_price
        state.trades_count += 1
        side_label = "SHORT" if is_short else "BUY"
        state.last_signal = f"{side_label} (manual)"

        # Log
        runner = BotRunner(config, state, self)
        runner._log(
            "TRADE",
            f"{side_label} {qty} {config.symbol} @ {fill_price:.2f} "
            f"(manual, expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
        )
```

And the return shape at line 359:

```python
        return {"qty": qty, "fill_price": fill_price, "slippage_bps": round(cost_bps, 2)}
```

(Renamed key + unsigned value.)

Add the helper import near the top of `bot_manager.py`:

```python
from slippage import slippage_cost_bps, fill_bias_bps
```

- [ ] **Step 4.8: Update the `list_bots` summary field**

At `backend/bot_manager.py:380`, replace:

```python
                "avg_slippage_pct": round(sum(state.slippage_pcts) / len(state.slippage_pcts), 4) if state.slippage_pcts else None,
```

With:

```python
                "avg_cost_bps": round(sum(state.slippage_bps) / len(state.slippage_bps), 2) if state.slippage_bps else None,
```

Field rename is deliberate: `avg_cost_bps` signals "unsigned cost" in the name, matching the spec's journal column rename to `avg_cost_bps` (spec §Frontend → TradeJournal). Frontend wiring in session 5 consumes this new key.

- [ ] **Step 4.9: Run backend tests again**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: all tests still green. This session doesn't add new tests (no existing bot-runner/manager unit tests), but none should regress.

- [ ] **Step 4.10: Grep sweep — `slippage_pct` / `slippage_pcts` gone from backend source**

```bash
cd backend && rg 'slippage_pct(s?)\b' --type py
```

Expected: **zero** hits in any `.py` file. If anything remains (outside `bots.json`), go back and fix it.

```bash
cd backend && rg 'slippage_pcts' data/bots.json | head -5
```

Expected: hits (legacy data on disk). These migrate lazily on server load — they're cleaned up on next `save()`. Acceptable at this commit boundary.

- [ ] **Step 4.11: Smoke-test the lazy migration**

```bash
cd backend && python -c "
from bot_manager import BotManager
m = BotManager()
m.load()
print(f'Loaded {len(m.bots)} bots')
for bid, (c, s) in list(m.bots.items())[:2]:
    print(f'  {c.symbol} {c.direction}: slippage_bps={c.slippage_bps}, state samples={s.slippage_bps[:3]}')
"
```

Expected: no traceback. Each bot prints a reasonable `slippage_bps` value (config values like 0.005 → 0.5 bps; 0.01 → 1 bps) and state lists of unsigned bps. If config values look wildly off (e.g. 10000), the scale factor in step 4.5 is wrong — percent to bps is ×100, not ×10000.

**Cross-check against the real data:** `grep '"slippage_pct":' backend/data/bots.json | head -5` shows values like `0.005`, `0.01`, `0.015` (percent). After migration they should become `0.5`, `1.0`, `1.5` (bps).

- [ ] **Step 4.12: Commit**

```bash
git add backend/bot_runner.py backend/bot_manager.py
git commit -m "feat(bots): unify slippage via shared helpers, rename to bps, lazy bots.json migration (B7 session 4)"
```

Do **not** commit `backend/data/bots.json` in this commit — it migrates on next load+save and should change as part of normal operation, not as a plan step. Include it only if pre-commit hooks have auto-saved it (then it's one coupled commit).

**Session 4 done.** Backend is coherent end-to-end: journal caches unsigned bps, `/api/slippage/` returns the new shape, backtester and bot runner both route through the shared module, `bots.json` migrates lazily. Frontend is still broken (sends `slippage_pct`, consumes `empirical_pct`). Next session fixes that.

---

## Session 5 — Frontend migration

**Context prime:**
- Spec §Frontend (all subsections); spec §Data model → "StrategyRequest" (for the localStorage migration formula); spec §Testing → manual verification list.
- Re-read sessions 3 and 4 of this plan for the API/state field names that the frontend must now match.

**Scope:** Every frontend touchpoint moves from percent to bps and from signed to unsigned-plus-diagnostic. This is the widest session; if you hit context pressure, it's safe to commit after step 5.3 (hook + types + StrategyBuilder) and resume 5.4+ in a fresh session — app will already be backtest-functional.

**Files (eight):**
- Rename: `frontend/src/shared/hooks/useEmpiricalSlippage.ts` → `frontend/src/shared/hooks/useSlippage.ts`
- Modify: `frontend/src/shared/types/index.ts`
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx`
- Modify: `frontend/src/features/trading/TradeJournal.tsx` (largest change — rewrite of the slippage column + header summary + CSV)
- Modify: `frontend/src/features/trading/BotCard.tsx` (label + field swap)
- Modify: `frontend/src/features/trading/AddBotBar.tsx`
- Modify: `frontend/src/api/bots.ts` (manual-buy response)
- Modify: `frontend/src/features/strategy/Results.tsx` (verify — likely no change needed; see step 5.9)

---

### Part A — Types + hook

- [ ] **Step 5.1: Rename and rewrite the hook**

```bash
git mv frontend/src/shared/hooks/useEmpiricalSlippage.ts frontend/src/shared/hooks/useSlippage.ts
```

Replace the contents of `frontend/src/shared/hooks/useSlippage.ts` with:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

export interface SlippageInfo {
  modeled_bps: number
  measured_bps: number | null
  fill_bias_bps: number | null
  fill_count: number
  source: 'default' | 'empirical'
}

export function useSlippage(symbol: string) {
  return useQuery<SlippageInfo>({
    queryKey: ['slippage', symbol.toUpperCase()],
    queryFn: async () => {
      const { data } = await api.get(`/api/slippage/${symbol.toUpperCase()}`)
      return data
    },
    enabled: !!symbol,
    staleTime: 60 * 1000,
  })
}
```

Note: `'manual'` is **not** in the `source` union — `manual` is frontend-only state set by `StrategyBuilder` when the user edits the input. Keeping it out of the API type prevents accidental round-trips.

- [ ] **Step 5.2: Update `frontend/src/shared/types/index.ts`**

Walk each pre-found hit (expected lines; use grep if they've drifted):

| Old (line) | Context | New |
|---|---|---|
| `:112` `slippage_pct?: number` | `StrategyRequest` type | `slippage_bps?: number` |
| `:147` `slippage: number \| ''` | `SavedStrategy` | `slippageBps: number \| ''` |
| `:164` `slippage?: number` | some intermediate SavedStrategy helper | `slippageBps?: number` |
| `:287` `slippage_pct?: number` | `BotConfig` | `slippage_bps?: number` |
| `:334` `avg_slippage_pct?: number \| null` | `BotSummary` | `avg_cost_bps?: number \| null` |

In addition, if `JournalTrade` exists in this file, add an optional `slippage_bps?: number | null` field next to `expected_price`. If `JournalTrade` is declared in `api/trading.ts` instead, add it there — check with grep before editing:

```bash
rg -n 'export (interface|type) JournalTrade' frontend/src
```

Add to `JournalTrade`:
```ts
  slippage_bps?: number | null   // unsigned cost cached at write time; null for legacy rows
```

### Part B — StrategyBuilder

- [ ] **Step 5.3: Rewire `StrategyBuilder.tsx`**

Required changes (line anchors are approximate — grep the snippet to confirm before editing):

**5.3.a — Import + state rename** (around line 8, 77, 82, 83):

```ts
import { useSlippage } from '../../shared/hooks/useSlippage'
// ...
const [slippageBps, setSlippageBps] = useState<number>(
  typeof saved?.slippageBps === 'number' ? saved.slippageBps
  // legacy SavedStrategy migration: percent → bps, floor at 0
  : typeof saved?.slippage === 'number' ? Math.max(0, saved.slippage * 100)
  : 2.0
)
const [slippageSource, setSlippageSource] = useState<'empirical' | 'default' | 'manual'>('default')
const { data: slipInfo } = useSlippage(ticker)
```

**Why the migration lives inline in the initializer** rather than in a loader: the loader for `SavedStrategy` (line 126 `loadSavedStrategy`) runs on explicit user action, but the `useState` initializer runs on every mount with whatever's in `localStorage`. Putting the migration in both places matters; 5.3.d handles the loader.

**5.3.b — Auto-populate effect** (around line 93-102):

Replace:

```ts
useEffect(() => {
  if (slippageSource === 'manual') return
  if (empiricalSlip?.empirical_pct != null && empiricalSlip.fill_count > 0) {
    setSlippage(empiricalSlip.empirical_pct)
    setSlippageSource('empirical')
  } else {
    setSlippage(0.01)
    setSlippageSource('default')
  }
}, [empiricalSlip?.empirical_pct, empiricalSlip?.fill_count, slippageSource])
```

With:

```ts
useEffect(() => {
  if (slippageSource === 'manual') return
  if (slipInfo) {
    setSlippageBps(slipInfo.modeled_bps)
    setSlippageSource(slipInfo.source)   // 'default' | 'empirical' — mirrors API
  }
}, [slipInfo?.modeled_bps, slipInfo?.source, slippageSource])
```

The endpoint already applies the floor + gate policy (spec §Policy), so the frontend just trusts `modeled_bps`. No client-side `max(default, measured)` — that would duplicate policy across layers.

**5.3.c — Input + hint JSX** (around line 227-251):

Replace the Slippage row:

```tsx
<div style={styles.settingsRow}>
  <label style={styles.settingsLabel}>Slippage (bps)</label>
  <input
    type="number"
    value={slippageBps}
    step={0.5}
    min={0}
    onChange={e => {
      const v = e.target.value
      if (v === '') {
        // Empty → revert to whatever the API currently recommends
        setSlippageSource('default')
        setSlippageBps(slipInfo?.modeled_bps ?? 2.0)
      } else {
        setSlippageBps(Math.max(0, +v))   // cost >= 0 enforced client-side too
        setSlippageSource('manual')
      }
    }}
    style={styles.settingsInput}
  />
  <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 6 }}>
    {slippageSource === 'empirical' && slipInfo
      ? `empirical · ${slipInfo.fill_count} fills`
      : slippageSource === 'default'
      ? (slipInfo && slipInfo.fill_count > 0
          ? `default · ${slipInfo.fill_count} fills below threshold`
          : 'default · no history yet')
      : 'manual'}
  </span>
</div>
```

No "⚠ favorable" warning — favorable fills now just produce `source: 'empirical'` with a floored modeled value; there's nothing the user needs to act on.

**5.3.d — Snapshot + loader** (lines 104-114 and 126-141):

In `currentSnapshot`, replace `slippage` with `slippageBps`:
```ts
slippageBps, commission, direction,
```

In `loadSavedStrategy`, replace `setSlippage(s.slippage)` with:
```ts
// Migrate legacy saved strategies: slippage is percent, slippageBps is bps.
const loadedBps = typeof s.slippageBps === 'number'
  ? s.slippageBps
  : Math.max(0, (s.slippage ?? 0) * 100)
setSlippageBps(loadedBps)
```

**5.3.e — Persistence effect** (lines 160-167):

Swap `slippage` for `slippageBps` in both the object and the dependency array. After any user has re-saved once, the `slippage` key is gone from the persisted state.

**5.3.f — Backtest submit payload** (line 186):

Replace:
```ts
slippage_pct: slippage !== '' && slippage !== 0 ? slippage : undefined,
```

With:
```ts
slippage_bps: slippageBps,   // always send — backend enforces ge=0
```

Always sending the value (rather than skipping when 0) is deliberate: the backend default is now 2.0, so omitting the field no longer means "no slippage" — it means "use 2.0". Explicit is safer.

**5.3.g — Clean up any stale `slippage` / `empiricalSlip` identifier references**

```bash
rg -n '\bslippage\b|\bempiricalSlip\b|empirical_pct' frontend/src/features/strategy/StrategyBuilder.tsx
```

Expected: zero hits (every remaining reference should now be `slippageBps` / `slipInfo` / `modeled_bps`).

### Part C — TradeJournal rewrite

- [ ] **Step 5.4: Add a client-side cost/bias helper inside `TradeJournal.tsx`**

Mirror of the Python `slippage_cost_bps` / `fill_bias_bps`. Put it near the bottom of the file next to the other pure helpers (`exitColor`, `sideColor`, `slippageColor`):

```ts
// Mirrors backend/slippage.py — used for legacy journal rows missing cached slippage_bps.
function costBpsFromTrade(t: JournalTrade): number | null {
  if (t.expected_price == null || t.price == null || t.expected_price <= 0) return null
  const side = (t.side || '').toLowerCase()
  const e = t.expected_price
  const f = t.price
  let raw: number
  if (side === 'buy' || side === 'cover')       raw = (f - e) / e * 1e4
  else if (side === 'sell' || side === 'short') raw = (e - f) / e * 1e4
  else return null
  return Math.max(0, raw)
}

function biasBpsFromTrade(t: JournalTrade): number | null {
  if (t.expected_price == null || t.price == null || t.expected_price <= 0) return null
  const side = (t.side || '').toLowerCase()
  const e = t.expected_price
  const f = t.price
  let raw: number
  if (side === 'buy' || side === 'cover')       raw = (f - e) / e * 1e4
  else if (side === 'sell' || side === 'short') raw = (e - f) / e * 1e4
  else return null
  return -raw   // positive = favorable
}

function slipBpsForRow(t: JournalTrade): number | null {
  // Prefer cached value; fall back to client-side derive for legacy rows.
  return t.slippage_bps != null ? t.slippage_bps : costBpsFromTrade(t)
}
```

- [ ] **Step 5.5: Rewrite the summary stats block (lines ~87-112)**

Replace the whole `summaryStats` IIFE with:

```ts
const summaryStats = (() => {
  let totalQty = 0
  let totalPnl = 0
  let gainPcts: number[] = []
  let costs: number[] = []
  let biases: number[] = []
  let fillCount = 0
  for (const t of filtered) {
    totalQty += t.qty || 0
    const pnl = exitPnl.get(t.id)
    if (pnl != null) {
      totalPnl += pnl
      const entryPx = exitEntryPrice.get(t.id)
      if (entryPx != null && t.qty) {
        gainPcts.push((pnl / (entryPx * t.qty)) * 100)
      }
    }
    const c = slipBpsForRow(t)
    const b = biasBpsFromTrade(t)
    if (c != null) { costs.push(c); fillCount++ }
    if (b != null) biases.push(b)
  }
  const avg = (xs: number[]) => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null
  return {
    totalQty,
    totalPnl,
    avgGainPct: avg(gainPcts),
    avgCostBps: avg(costs),
    avgBiasBps: avg(biases),
    fillCount,
  }
})()
```

- [ ] **Step 5.6: Update the Slippage column header, summary row, per-row cell**

Column header text (line ~185) stays `'Slippage'` but change the unit in the summary row to bps, and change the per-row render to unsigned bps with a red tint.

Replace the summary cell at line ~203-205:

```tsx
<span style={{ ...styles.summaryCell, color: '#8b949e' }}>
  {summaryStats.avgCostBps != null ? `${summaryStats.avgCostBps.toFixed(1)} bps` : ''}
</span>
```

Replace the per-row cell at line ~242-246:

```tsx
<span style={{ ...styles.cell, color: costColor(slipBpsForRow(t)) }}>
  {(() => {
    const c = slipBpsForRow(t)
    return c == null ? '—' : `${c.toFixed(1)} bps`
  })()}
</span>
```

Delete the old `slippageColor` helper (lines 287-294) entirely and add this in its place:

```ts
const costColor = (bps: number | null) => {
  if (bps == null) return '#8b949e'
  if (bps < 0.5) return '#8b949e'                 // neutral gray at ~0
  if (bps < 3)   return 'rgba(248, 81, 73, 0.6)'  // faint red
  return '#f85149'                                // saturated red at large cost
}
```

No green — favorable fills render as plain gray `0.0 bps`, which is exactly the "cost is always >= 0" contract rendered.

- [ ] **Step 5.7: Add the header diagnostic block above the table**

Per spec §Frontend → TradeJournal: "Fills — N trades · avg cost X bps · fill bias +Y bps".

Insert between `<div style={styles.header}>...</div>` (closes at line ~179) and the `{filtered.length === 0 ...}` ternary (line ~180):

```tsx
{filtered.length > 0 && summaryStats.fillCount > 0 && (
  <div style={styles.diagnosticBar}>
    <span>Fills — {summaryStats.fillCount} trades</span>
    <span>· avg cost <b>{(summaryStats.avgCostBps ?? 0).toFixed(1)} bps</b></span>
    {summaryStats.avgBiasBps != null && (
      <span>
        · fill bias <b style={{ color: summaryStats.avgBiasBps >= 0 ? '#26a641' : '#f85149' }}>
          {summaryStats.avgBiasBps >= 0 ? '+' : ''}{summaryStats.avgBiasBps.toFixed(1)} bps
        </b>
      </span>
    )}
  </div>
)}
```

Add this to the `styles` object at the bottom of the file:

```ts
diagnosticBar: {
  display: 'flex', gap: 12, flexWrap: 'wrap' as const,
  padding: '6px 16px',
  borderBottom: '1px solid #21262d',
  fontSize: 11, color: '#8b949e',
  background: '#0d1117',
},
```

The diagnostic block is the **only** place signed `fill_bias_bps` appears in the journal UI — per-row display stays unsigned cost per spec §Goals.

- [ ] **Step 5.8: Update the CSV export**

In `exportCsv` (line ~119), replace the `'Slippage'` header with `'Slippage (bps)'`, and replace the `slippage` cell derivation:

```ts
const slip = slipBpsForRow(t)
const slippage = slip == null ? '' : slip.toFixed(2)
```

CSV values are now unsigned bps numbers (not percent strings), so the column is machine-readable.

### Part D — Bot UI surfaces

- [ ] **Step 5.9: `BotCard.tsx` label swap**

At `frontend/src/features/trading/BotCard.tsx:215-216`, replace:

```tsx
{summary.avg_slippage_pct != null && (
  <span style={{ color: '#666' }}>Slippage: <span style={{ color: Math.abs(summary.avg_slippage_pct) > 0.05 ? '#f85149' : '#8b949e' }}>{summary.avg_slippage_pct.toFixed(3)}%</span></span>
```

With:

```tsx
{summary.avg_cost_bps != null && (
  <span style={{ color: '#666' }}>Cost: <span style={{ color: summary.avg_cost_bps > 3 ? '#f85149' : '#8b949e' }}>{summary.avg_cost_bps.toFixed(1)} bps</span></span>
```

Threshold change from 0.05% (= 5 bps) to 3 bps: matches the journal's `costColor` breakpoint. "Slippage" → "Cost" label change is intentional — it signals the unsigned/cost convention without a long tooltip.

- [ ] **Step 5.10: `AddBotBar.tsx` — POST payload**

At `frontend/src/features/trading/AddBotBar.tsx:84`, replace:

```ts
slippage_pct: typeof s.slippage === 'number' ? s.slippage : 0,
```

With:

```ts
slippage_bps: typeof s.slippageBps === 'number' ? s.slippageBps
           : typeof s.slippage === 'number' ? Math.max(0, s.slippage * 100)
           : 2.0,
```

Legacy `SavedStrategy` records carry `slippage` (percent); the bridge keeps old saved strategies importable into new bots.

- [ ] **Step 5.11: `api/bots.ts` — manual buy response**

At `frontend/src/api/bots.ts:45`, replace:

```ts
export async function manualBuyBot(botId: string): Promise<{ qty: number; fill_price: number; slippage_pct: number }> {
```

With:

```ts
export async function manualBuyBot(botId: string): Promise<{ qty: number; fill_price: number; slippage_bps: number }> {
```

Then grep for any consumer of `.slippage_pct` on this return shape and update:

```bash
rg -n 'manualBuyBot|\.slippage_pct' frontend/src
```

Expected: zero remaining `.slippage_pct` references across the frontend after this edit.

### Part E — Verify Results.tsx + sweep

- [ ] **Step 5.12: Verify `Results.tsx`**

`Results.tsx` reads `t.slippage` — the **dollar-amount** slippage on each backtest trade, not percent. Session 3 left this field intact (it's computed from `abs(shares * (fill_price - price))`). So `Results.tsx` should need **no changes**. Confirm:

```bash
rg -n 'slippage' frontend/src/features/strategy/Results.tsx
```

Expected: hits reference `t.slippage` (dollar amount) and `totalSlip` aggregation — both still correct. No `slippage_pct` or `avg_slippage_pct` references. If any appear, investigate before editing.

- [ ] **Step 5.13: Final frontend sweep**

```bash
rg -n 'slippage_pct|avg_slippage_pct|empirical_pct|useEmpiricalSlippage' frontend/src
```

Expected: **zero** hits. Any remaining hit is a miss — go back and fix.

```bash
rg -n 'slippage' frontend/src | rg -v 'slippage_bps|avg_cost_bps|slippageBps|useSlippage|SlippageInfo'
```

Expected: only legitimate references remain — `t.slippage` (dollar amount in backtest trades), `s.slippage` (legacy SavedStrategy key in migration fallbacks), the `Slippage` column label, and the `slipInfo` / `slipBpsForRow` helpers. Anything else is a bug.

- [ ] **Step 5.14: Run frontend build and tests**

```bash
cd frontend && npm run build
```
Expected: clean build, no TypeScript errors.

If there's a test suite:
```bash
cd frontend && npm test -- --run
```
Expected: green. (This repo does not have frontend unit tests at time of writing — if `npm test` fails due to no tests, that's acceptable.)

- [ ] **Step 5.15: Smoke-test in the browser**

```bash
./start.sh
```

Verify in the UI:
1. **Load any symbol with journal history.** The Slippage field in StrategyBuilder shows a number in bps, and the hint reads `empirical · N fills` or `default · ...`. Never shows `%`.
2. **Run a backtest.** Results renders without exceptions. Cost Breakdown section shows dollar slippage (unchanged).
3. **Open TradeJournal.** Every row's Slip column shows `N.N bps` in gray-to-red. No green. Header shows `Fills — N trades · avg cost X bps · fill bias ±Y bps`. CSV export downloads without error and opens with a `Slippage (bps)` numeric column.
4. **Open a BotCard.** The metric reads `Cost: N.N bps` (not `Slippage: N.NNN%`).

If any surface still reads `%` for slippage, something's uncaught — grep, fix, rebuild.

- [ ] **Step 5.16: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): migrate slippage UI to bps, unsigned per-row + signed bias header (B7 session 5)"
```

**Session 5 done.** Frontend and backend both speak bps; every user-visible slippage surface is unsigned cost with diagnostic bias aggregated separately. All that remains is the manual verification pass in session 6 and the TODO.md update.

---

## Session 6 — Manual verification pass + TODO update

**Scope:** no code changes. Walk the 4-step manual verification checklist from spec §Testing, mark B7 shipped in TODO.md, commit.

**Context prime:** none — this session is pure verification against live app.

**Files:**
- Modify: `TODO.md:33`

- [ ] **Step 6.1: Start app**

```bash
./start.sh
```

Wait until frontend serves at `http://localhost:5173` and backend `http://localhost:8000`.

- [ ] **Step 6.2: Verify legacy-config migration (spec §Testing item 1)**

Load a saved strategy (`SavedStrategies` UI) whose raw JSON contains a negative `slippage_pct`. Open browser devtools → Application → Local Storage → inspect the saved entry first to confirm the negative value exists.

After loading:
- StrategyBuilder's Slippage input reads `0` (or the floored empirical value).
- Hint shows `default · no history yet` **or** `empirical · N fills` — never a negative number.
- No `%` anywhere on that surface.

Expected: migration code (`Math.max(0, (s.slippage ?? 0) * 100)`) normalizes the legacy field; nothing shows the old negative value.

- [ ] **Step 6.3: Verify floor-at-default policy (spec §Testing item 2)**

Pick a symbol with known historically favorable fills (ask user or spot-check journal for a symbol where `fill_bias` historically skewed negative — favorable). Enter that symbol in StrategyBuilder.

Expected:
- Slippage input auto-populates to `2.0` (default).
- Hint reads `default · N fills (empirical favorable, using default)` **or** `default · no history yet` depending on fill count.
- It does **not** show a value below 2.0, even if empirical measurement is negative/favorable.

Run a backtest. Results renders without exception. Cost Breakdown shows a non-zero slippage dollar figure.

- [ ] **Step 6.4: Verify live bot trade round-trip (spec §Testing item 3)**

Start a bot on a test symbol. Wait for or trigger one entry + one exit (can use a loose strategy to fire fast, or manual-buy/close via UI).

Then:
- Tail `backend` logs: each TRADE line includes `cost=N.Nbps, bias=±N.Nbps` (not `slippage_pct=...`).
- Open `backend/data/trade_journal.json`, inspect the two newest rows: each has a `slippage_bps` field. If `expected_price` was set, value is a non-negative number; if not, value is `null`.
- Hit `GET http://localhost:8000/api/slippage/{SYM}` in browser or curl. Response has shape `{modeled_bps, measured_bps, fill_bias_bps, fill_count, source}`. `fill_count` reflects the new trades (incremented by up to 2 vs. pre-test).

- [ ] **Step 6.5: Verify TradeJournal UI (spec §Testing item 4)**

Open the TradeJournal tab.

Expected:
- Every row's Slip column shows unsigned `N.N bps`, colored gray → red with breakpoints 0.5 / 3 bps. No green, no negative numbers, no `%`.
- Diagnostic bar above the table: `Fills — N trades · avg cost X.X bps · fill bias ±Y.Y bps`. Bias may be negative (favorable) or positive (adverse); cost never negative.
- CSV export downloads, opens in a spreadsheet, has a column header `Slippage (bps)` containing plain numbers (no `%` suffix).

If any of the four checks fails, **stop** — do not mark B7 shipped. File the discrepancy against the relevant session and rewind.

- [ ] **Step 6.6: Mark B7 shipped in TODO.md**

Edit `TODO.md:33` — flip the checkbox:

```markdown
- [x] **B7** Slippage model redesign — separate *measured* slippage (diagnostics) from *modeled* slippage (backtest assumption). Always ≥ 0 everywhere it surfaces. Floor empirical at default, gate on minimum fill_count, single shared signed-cost helper. Fixes journal display (wrong sign for sells/shorts), bot runner log sign drift, and the "favorable empirical auto-carries into Capital & Fees" pitfall.
```

- [ ] **Step 6.7: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): mark B7 shipped — slippage redesign complete"
```

**Session 6 done. B7 shipped.** Measured and modeled slippage are now separate concepts, units are bps end-to-end, favorable empirical evidence can no longer make the backtest cheaper than default, and the journal's sign convention is fixed for short/cover rows.
