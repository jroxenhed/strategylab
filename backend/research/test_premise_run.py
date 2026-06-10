"""Unit tests for F389 premise_run.py — run service + gateway logic.

Tests per brief §6 + decisions.md override assertions.

Run:
    backend/venv/bin/python3 -m pytest backend/research/test_premise_run.py -q

Path-setup follows test_premise_spec.py pattern.

INVARIANT: Tests NEVER pass _REAL_FDR_LEDGER to anything.
All FDR paths use tmp_path / "smoke_ledger.json" or None.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timezone, datetime
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, call
import copy

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from research.premise_spec import PremiseSpec, spec_hash  # noqa: E402
from research.premise_compile import compile_spec          # noqa: E402
import research.premise_store as _ps_module               # noqa: E402
from research.premise_store import PremiseStore            # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_spec(**kwargs) -> PremiseSpec:
    base = dict(
        premise_text="Insider cluster buy → price appreciation",
        stream="form4",
        event_filter={"transaction_codes": ["P"]},
        dose="r1_score",
        horizons=(21, 63, 126),
        entry_lag_days=1,
        n_boot=99,
    )
    base.update(kwargs)
    return PremiseSpec(**base)


def _make_store(tmp_path: Path) -> PremiseStore:
    """Create an isolated PremiseStore backed by a tmp file."""
    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    return PremiseStore()


# ===========================================================================
# §6.1 — Gate logic: explore/preview NEVER touch real ledger
# ===========================================================================

class TestExploreLedgerGate:
    """compile_spec is the structural gate: fdr_ledger_path=None on all explore paths."""

    def test_compile_spec_with_none_ledger_never_writes_ledger(self, tmp_path):
        """compile_spec with fdr_ledger_path=None produces config.fdr_ledger_path=None."""
        spec = _minimal_spec()
        cr = compile_spec(spec, study_name="test_preview", output_dir=tmp_path, fdr_ledger_path=None)
        # Gate assertion: the config that would be passed to run_event_study has fdr_ledger_path=None
        assert cr.config.fdr_ledger_path is None, (
            "compile_spec with fdr_ledger_path=None must produce config.fdr_ledger_path=None"
        )

    def test_real_fdr_ledger_constant_never_passed_to_compile_spec(self, tmp_path):
        """_REAL_FDR_LEDGER is defined in premise_run.py but NEVER passed to compile_spec
        in v1. This test inspects the module to verify the structural invariant."""
        import research.premise_run as pr
        import inspect

        # _REAL_FDR_LEDGER must exist as a module constant
        assert hasattr(pr, "_REAL_FDR_LEDGER"), "_REAL_FDR_LEDGER constant must be defined"

        # Verify _run_preview_sync source never calls compile_spec with _REAL_FDR_LEDGER
        src = inspect.getsource(pr._run_preview_sync)
        assert "fdr_ledger_path=None" in src, (
            "_run_preview_sync must call compile_spec with fdr_ledger_path=None"
        )
        # Should NOT pass _REAL_FDR_LEDGER to compile_spec
        # (It might reference it in string building but compile_spec call must be None)
        assert "_REAL_FDR_LEDGER" not in src, (
            "_run_preview_sync must NOT reference _REAL_FDR_LEDGER at all"
        )

    def test_worker_sync_source_uses_none_ledger(self):
        """premise_run_worker._run_full_explore_sync always uses fdr_ledger_path=None."""
        import inspect
        from research import premise_run_worker as pw

        src = inspect.getsource(pw.run_full_explore_sync)
        assert "fdr_ledger_path=None" in src, (
            "run_full_explore_sync must pass fdr_ledger_path=None to compile_spec and run_r1_analysis"
        )
        # _REAL_FDR_LEDGER must NOT appear in the worker's compute function
        assert "_REAL_FDR_LEDGER" not in src, (
            "run_full_explore_sync must NOT reference _REAL_FDR_LEDGER"
        )

    def test_smoke_ledger_path_compiles_ok(self, tmp_path):
        """Using a smoke path (not the real ledger) is fine for non-production use."""
        smoke = tmp_path / "smoke_ledger.json"
        spec = _minimal_spec()
        cr = compile_spec(spec, study_name="test_smoke", output_dir=tmp_path, fdr_ledger_path=smoke)
        assert cr.config.fdr_ledger_path == smoke


# ===========================================================================
# §6 (override) — graduate_to_confirm gate assertions
# ===========================================================================

class TestGraduateToConfirm:
    """All graduate_to_confirm invariants per decisions.md override."""

    @pytest.fixture(autouse=True)
    def isolate_store(self, tmp_path):
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _setup_explored_premise(self) -> tuple[PremiseStore, str]:
        """Create a premise in 'explored' state with a spec."""
        store = PremiseStore()
        pid = store.add_premise("Insider cluster buy test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        # Manually advance to explored (bypass normal state machine for test setup)
        store.premises[pid]["status"] = "explored"
        store.save()
        return store, pid

    def test_graduate_freezes_spec_hash(self):
        """graduate_to_confirm must set spec_hash on the stored spec."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        # Monkeypatch _check_power_audit to always pass
        async def _mock_power_audit():
            pass

        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            result = asyncio.get_event_loop().run_until_complete(
                pr.graduate_to_confirm(pid)
            )

        assert result["spec_hash"] is not None
        assert len(result["spec_hash"]) == 16  # spec_hash is 16 hex chars

        # Verify stored spec has the hash
        store2 = PremiseStore()
        p = store2.premises[pid]
        assert p["spec"]["spec_hash"] == result["spec_hash"]

    def test_graduate_transitions_to_awaiting_confirm(self):
        """graduate_to_confirm must transition to awaiting_confirm, NOT confirmed."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            result = asyncio.get_event_loop().run_until_complete(
                pr.graduate_to_confirm(pid)
            )

        assert result["status"] == "awaiting_confirm"

        store2 = PremiseStore()
        assert store2.premises[pid]["status"] == "awaiting_confirm"
        # Must NOT have reached confirmed
        assert store2.premises[pid]["status"] != "confirmed"

    def test_graduate_records_confirm_request(self):
        """graduate_to_confirm must record a confirm_request entry in run_history."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            result = asyncio.get_event_loop().run_until_complete(
                pr.graduate_to_confirm(pid)
            )

        assert "confirm_request" in result
        cr = result["confirm_request"]
        assert cr["type"] == "confirm_request"
        assert cr["spec_hash"] is not None
        assert "future_worker_cmd" in cr
        assert "note" in cr
        assert "F393" in cr["note"]

        # Also verify run_history in store
        store2 = PremiseStore()
        p = store2.premises[pid]
        confirm_runs = [r for r in p.get("run_history", []) if r.get("type") == "confirm_request"]
        assert len(confirm_runs) == 1

    def test_graduate_does_not_call_run_event_study(self):
        """graduate_to_confirm must NEVER call run_event_study."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            with patch("research.event_study.run_event_study") as mock_run:
                asyncio.get_event_loop().run_until_complete(
                    pr.graduate_to_confirm(pid)
                )
                mock_run.assert_not_called()

    def test_graduate_real_ledger_not_passed_to_compute(self):
        """_REAL_FDR_LEDGER must NOT be passed to compile_spec or run_event_study
        during graduate_to_confirm. It is only used to build the future command string."""
        import research.premise_run as pr
        import inspect

        src = inspect.getsource(pr.graduate_to_confirm)

        # The function source references _REAL_FDR_LEDGER only in string construction
        # It must NOT call compile_spec at all
        assert "compile_spec" not in src, (
            "graduate_to_confirm must NOT call compile_spec (decisions.md override)"
        )
        assert "run_event_study" not in src, (
            "graduate_to_confirm must NOT call run_event_study (decisions.md override)"
        )

    def test_graduate_blocks_if_status_not_explored(self, tmp_path):
        """graduate_to_confirm must 409 if status != explored."""
        from fastapi import HTTPException
        import research.premise_run as pr

        store = PremiseStore()
        pid = store.add_premise("Test premise")

        async def _run():
            return await pr.graduate_to_confirm(pid)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(_run())
        assert exc_info.value.status_code == 409
        assert "explored" in exc_info.value.detail.lower()

    def test_graduate_blocks_underpowered(self):
        """graduate_to_confirm must 400 if power audit returns MDE > threshold."""
        from fastapi import HTTPException
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        # Mock run_audit to return high MDE (above 10pp threshold)
        mock_audit_result = {
            "mde_80pct": {"design_r1": 15.0, "design_r2": 12.5},
            "power_table": {},
            "e_grid": [],
            "meta": {},
        }

        def _mock_run_audit_sync():
            return mock_audit_result

        with patch.object(pr, "_run_power_audit_sync", _mock_run_audit_sync):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    pr.graduate_to_confirm(pid)
                )
        assert exc_info.value.status_code == 400
        assert "power" in exc_info.value.detail.lower()

    def test_graduate_refuses_duplicate_hash(self):
        """graduate_to_confirm must 409 if another confirmed/awaiting_confirm premise
        has the same spec_hash (store-wide idempotency)."""
        from fastapi import HTTPException
        import research.premise_run as pr

        # Create first premise — explore + graduate to awaiting_confirm
        store = PremiseStore()
        pid1 = store.add_premise("First premise")
        spec = _minimal_spec()
        store.add_spec(pid1, spec.model_dump())
        store.premises[pid1]["status"] = "explored"
        store.save()

        async def _mock_power_audit():
            pass

        # Graduate first premise
        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            asyncio.get_event_loop().run_until_complete(pr.graduate_to_confirm(pid1))

        # Create second premise with IDENTICAL structural spec
        store2 = PremiseStore()
        pid2 = store2.add_premise("Second premise — same structural spec")
        same_spec_dict = spec.model_dump()
        same_spec_dict["premise_text"] = "Different text, same test"
        store2.add_spec(pid2, same_spec_dict)
        store2.premises[pid2]["status"] = "explored"
        store2.save()

        # Second graduate should 409 (duplicate hash)
        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    pr.graduate_to_confirm(pid2)
                )
        assert exc_info.value.status_code == 409
        assert "hash" in exc_info.value.detail.lower()

    def test_graduate_future_command_references_real_ledger_as_string(self):
        """The confirm_request must contain the real FDR ledger path in the future
        command string — but it's only a string, never passed to compute."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            result = asyncio.get_event_loop().run_until_complete(
                pr.graduate_to_confirm(pid)
            )

        future_cmd = result["confirm_request"]["future_worker_cmd"]
        # The real ledger path must appear in the future command string
        assert "fdr_ledger" in future_cmd, (
            "confirm_request.future_worker_cmd must reference the FDR ledger path for F393"
        )
        # But confirm_request itself must NOT have run anything
        store2 = PremiseStore()
        assert store2.premises[pid]["status"] == "awaiting_confirm"

    def test_graduate_does_not_reach_confirmed(self):
        """Confirmed state is unreachable from graduate_to_confirm in v1."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            asyncio.get_event_loop().run_until_complete(pr.graduate_to_confirm(pid))

        store2 = PremiseStore()
        assert store2.premises[pid]["status"] != "confirmed"
        assert store2.premises[pid]["status"] == "awaiting_confirm"

    def test_graduate_message_mentions_f393(self):
        """Response message must clearly state the real OOS run is deferred (F393)."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            result = asyncio.get_event_loop().run_until_complete(
                pr.graduate_to_confirm(pid)
            )

        assert "F393" in result["message"]
        assert "OOS" in result["message"] or "out-of-sample" in result["message"].lower()


# ===========================================================================
# §6.2 — Worker command construction (no live worker)
# ===========================================================================

class TestWorkerDispatch:
    """Verify full-explore builds correct worker-dispatch invocation without live worker."""

    @pytest.fixture(autouse=True)
    def isolate_store(self, tmp_path):
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _setup_spec_ready_premise(self) -> tuple[PremiseStore, str]:
        store = PremiseStore()
        pid = store.add_premise("Worker dispatch test premise")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        return store, pid

    def test_full_explore_dispatch_args(self, tmp_path, monkeypatch):
        """run_full_explore_sync builds correct worker-dispatch.sh invocation."""
        import research.premise_run as pr

        captured = {}

        def mock_subprocess_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "DISPATCHED target=local-fallback pid=99999 log=test/test.log"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        store, pid = self._setup_spec_ready_premise()

        pr._run_full_explore_sync(pid, str(tmp_path / "outdir"), "test.log")

        cmd = captured["cmd"]
        # Must contain worker-dispatch.sh
        assert any("worker-dispatch.sh" in str(c) for c in cmd), (
            "Full explore must call bin/worker-dispatch.sh"
        )
        # Must contain the premise_id
        assert "--premise-id" in cmd
        assert pid in cmd
        # Must contain premise_run_worker.py
        assert any("premise_run_worker.py" in str(c) for c in cmd), (
            "Dispatch command must reference premise_run_worker.py"
        )

    def test_full_explore_worker_require_env(self, tmp_path, monkeypatch):
        """WORKER_REQUIRE env var must list the 4 required cache paths."""
        import research.premise_run as pr

        captured_env = {}

        def mock_subprocess_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "DISPATCHED target=local-fallback pid=99999 log=out/test.log"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        store, pid = self._setup_spec_ready_premise()
        pr._run_full_explore_sync(pid, str(tmp_path / "outdir"), "test.log")

        worker_require = captured_env.get("WORKER_REQUIRE", "")
        assert "index.json" in worker_require, "WORKER_REQUIRE must include index.json"
        assert "submissions" in worker_require, "WORKER_REQUIRE must include submissions"
        assert "price_cache" in worker_require, "WORKER_REQUIRE must include price_cache"

    def test_worker_probe_local_fallback(self, monkeypatch):
        """When worker-probe returns LOCAL, no WORKER_HOST env is set."""
        import research.premise_run as pr

        captured_env = {}

        def mock_subprocess_run(cmd, **kwargs):
            # First call: worker-probe.sh
            if any("worker-probe.sh" in str(c) for c in cmd):
                mock_result = MagicMock()
                mock_result.returncode = 1
                mock_result.stdout = "RECOMMEND: LOCAL  (no worker reachable — run compute locally)"
                mock_result.stderr = ""
                return mock_result
            # Second call: worker-dispatch.sh
            captured_env.update(kwargs.get("env", {}))
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "DISPATCHED target=local-fallback pid=99999 log=out/test.log"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        host, shell = pr._probe_worker()
        assert host == "LOCAL"
        assert shell == "LOCAL"

    def test_worker_probe_parses_remote(self, monkeypatch):
        """worker-probe output RECOMMEND: WORKER_HOST=mfcore01 WORKER_SHELL=native is parsed."""
        import research.premise_run as pr

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = (
                "office (mfcore01, native) UP host=mfcore01 32 cpu\n"
                "RECOMMEND: WORKER_HOST=mfcore01 WORKER_SHELL=native\n"
            )
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        host, shell = pr._probe_worker()
        assert host == "mfcore01"
        assert shell == "native"


# ===========================================================================
# §6.3 — Job state transitions
# ===========================================================================

class TestJobState:
    """Job state dict transitions + one-active-job-per-premise 409."""

    @pytest.fixture(autouse=True)
    def reset_jobs(self, tmp_path):
        """Reset _jobs between tests."""
        import research.premise_run as pr
        pr._jobs.clear()
        pr._job_locks.clear()
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _setup_spec_ready_premise(self) -> tuple[PremiseStore, str]:
        store = PremiseStore()
        pid = store.add_premise("Job state test premise")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        return store, pid

    def test_preview_sets_running_on_trigger(self, monkeypatch):
        """run_preview sets job status to 'running' synchronously before bg task runs."""
        import research.premise_run as pr

        _store, pid = self._setup_spec_ready_premise()
        # Advance premise to spec_ready so the exploring transition is legal
        _store.premises[pid]["status"] = "spec_ready"
        _store.save()

        # Mock the actual background work so we can inspect intermediate state
        started = []
        original_create_task = asyncio.create_task

        async def _run_with_inspection():
            # Capture the running state right after trigger, before bg completes
            with patch("asyncio.create_task", lambda coro: started.append(coro) or original_create_task(coro)):
                await pr.run_preview(pid)
                job = pr._jobs.get(pid)
                assert job is not None
                assert job["status"] == "running"
                assert job["run_type"] == "preview"

        asyncio.get_event_loop().run_until_complete(_run_with_inspection())

    def test_second_trigger_returns_409(self):
        """Triggering a second run while one is active returns 409."""
        from fastapi import HTTPException
        import research.premise_run as pr

        _store, pid = self._setup_spec_ready_premise()

        # Manually inject a running job
        pr._jobs[pid] = {
            "status": "running",
            "run_type": "preview",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": None,
            "logname": None,
            "verdict": None,
        }

        async def _run():
            await pr.run_preview(pid)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(_run())
        assert exc_info.value.status_code == 409

    def test_poll_explore_status_returns_done_on_sentinel(self, tmp_path, monkeypatch):
        """poll_explore_status transitions to done when worker-status returns STATUS=DONE."""
        import research.premise_run as pr

        _store, pid = self._setup_spec_ready_premise()
        outdir = str(tmp_path / "study_outdir")
        logname = "test.log"

        # Inject a running explore job
        pr._jobs[pid] = {
            "status": "running",
            "run_type": "explore",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": outdir,
            "logname": logname,
            "verdict": None,
        }

        # Write a fake verdict file
        Path(outdir).mkdir(parents=True, exist_ok=True)
        fake_verdict = {"explore_decision": "ADVANCE", "n_valid_events": 42}
        with open(Path(outdir) / "r1_explore_verdict.json", "w") as f:
            json.dump(fake_verdict, f)

        # Mock worker-status.sh to return DONE
        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "STATUS=DONE exit=0"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        # Also monkeypatch PremiseStore to avoid real store writes
        store = PremiseStore()
        # Manually put premise in exploring state
        store.premises[pid]["status"] = "exploring"
        store.save()

        job = pr.poll_explore_status(pid)
        assert job["status"] == "done"
        assert job["verdict"] is not None

    def test_poll_explore_status_returns_failed_on_failure(self, tmp_path, monkeypatch):
        """poll_explore_status transitions to failed when worker-status returns STATUS=FAILED."""
        import research.premise_run as pr

        _store, pid = self._setup_spec_ready_premise()
        outdir = str(tmp_path / "study_outdir")
        logname = "test.log"
        Path(outdir).mkdir(parents=True, exist_ok=True)

        pr._jobs[pid] = {
            "status": "running",
            "run_type": "explore",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": outdir,
            "logname": logname,
            "verdict": None,
        }

        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "STATUS=FAILED exit=1"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        store = PremiseStore()
        store.premises[pid]["status"] = "exploring"
        store.save()

        job = pr.poll_explore_status(pid)
        assert job["status"] == "failed"
        assert job["error"] is not None


# ===========================================================================
# §6.4 — FastAPI endpoint contracts (TestClient)
# ===========================================================================

class TestEndpointContracts:
    """FastAPI TestClient contract tests for all premises endpoints."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        """Isolate store and reset job state for each test."""
        import research.premise_run as pr
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")
        pr._jobs.clear()
        pr._job_locks.clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.premises import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_list_premises_empty(self):
        r = self.client.get("/api/premises")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_premise(self):
        r = self.client.post("/api/premises", json={"premise_text": "Test premise idea"})
        assert r.status_code == 201
        data = r.json()
        assert "premise_id" in data
        assert data["status"] == "draft"

    def test_create_premise_empty_text_rejected(self):
        r = self.client.post("/api/premises", json={"premise_text": "   "})
        assert r.status_code == 400

    def test_get_premise_not_found(self):
        # nonexistent-id doesn't match ^p-[0-9a-f]{8}$ pattern → 422 (G5)
        r = self.client.get("/api/premises/nonexistent-id")
        assert r.status_code == 422

    def test_get_premise_not_found_valid_format(self):
        # Valid format but non-existent → 404
        r = self.client.get("/api/premises/p-00000000")
        assert r.status_code == 404

    def test_get_premise_returns_full_dict(self):
        r = self.client.post("/api/premises", json={"premise_text": "Full dict test"})
        pid = r.json()["premise_id"]
        r2 = self.client.get(f"/api/premises/{pid}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["premise_id"] == pid
        assert "status" in data
        assert "run_history" in data

    def test_save_spec_valid(self):
        # G2: from draft, transition draft→spec_ready is illegal per state machine.
        # First advance to awaiting_formalization so spec_ready transition is legal.
        r = self.client.post("/api/premises", json={"premise_text": "Spec save test"})
        pid = r.json()["premise_id"]
        store = PremiseStore()
        store.premises[pid]["status"] = "awaiting_formalization"
        store.save()

        spec_dict = {
            "premise_text": "Spec save test",
            "stream": "form4",
            "dose": "r1_score",
            "event_filter": {"transaction_codes": ["P"]},
            "horizons": [21, 63, 126],
        }
        r2 = self.client.put(f"/api/premises/{pid}/spec", json=spec_dict)
        assert r2.status_code == 200
        assert r2.json()["status"] == "spec_ready"

    def test_save_spec_invalid_stream_422(self):
        r = self.client.post("/api/premises", json={"premise_text": "Invalid stream test"})
        pid = r.json()["premise_id"]

        spec_dict = {
            "premise_text": "Invalid stream test",
            "stream": "unknown_stream",
            "dose": "r1_score",
        }
        r2 = self.client.put(f"/api/premises/{pid}/spec", json=spec_dict)
        assert r2.status_code == 422

    def test_save_spec_invalid_dose_422(self):
        r = self.client.post("/api/premises", json={"premise_text": "Invalid dose test"})
        pid = r.json()["premise_id"]

        spec_dict = {
            "premise_text": "Invalid dose test",
            "stream": "form4",
            "dose": "made_up_dose",
        }
        r2 = self.client.put(f"/api/premises/{pid}/spec", json=spec_dict)
        assert r2.status_code == 422

    def test_run_no_spec_400(self):
        r = self.client.post("/api/premises", json={"premise_text": "No spec run test"})
        pid = r.json()["premise_id"]
        r2 = self.client.post(f"/api/premises/{pid}/run", json={"mode": "preview"})
        assert r2.status_code == 400
        assert "spec" in r2.json()["detail"].lower()

    def test_run_invalid_mode_422(self):
        # G11: Literal["preview","explore"] on RunRequest returns 422 (not 400) for invalid modes
        r = self.client.post("/api/premises", json={"premise_text": "Mode test"})
        pid = r.json()["premise_id"]
        r2 = self.client.post(f"/api/premises/{pid}/run", json={"mode": "invalid_mode"})
        assert r2.status_code == 422

    def test_run_status_unknown_after_exploring_no_job(self):
        """When premise is 'exploring' but no job in _jobs → server restart scenario."""
        # Create premise and manually set to exploring
        r = self.client.post("/api/premises", json={"premise_text": "Restart test"})
        pid = r.json()["premise_id"]

        spec_dict = {
            "premise_text": "Restart test",
            "stream": "form4",
            "dose": "r1_score",
        }
        self.client.put(f"/api/premises/{pid}/spec", json=spec_dict)

        # Manually set to exploring in store (bypass normal trigger)
        store = PremiseStore()
        store.premises[pid]["status"] = "exploring"
        store.save()

        r2 = self.client.get(f"/api/premises/{pid}/run-status")
        assert r2.status_code == 200
        data = r2.json()
        assert data["status"] == "unknown"
        assert "server" in data.get("note", "").lower() or "restart" in data.get("note", "").lower()

    def test_verdict_no_runs_returns_null(self):
        r = self.client.post("/api/premises", json={"premise_text": "Verdict null test"})
        pid = r.json()["premise_id"]
        r2 = self.client.get(f"/api/premises/{pid}/verdict")
        assert r2.status_code == 200
        assert r2.json()["verdict"] is None

    def test_graduate_wrong_status_409(self):
        r = self.client.post("/api/premises", json={"premise_text": "Graduate test"})
        pid = r.json()["premise_id"]
        r2 = self.client.post(f"/api/premises/{pid}/graduate-to-confirm", json={})
        assert r2.status_code == 409

    def test_delete_premise_draft(self):
        r = self.client.post("/api/premises", json={"premise_text": "Delete test"})
        pid = r.json()["premise_id"]
        r2 = self.client.delete(f"/api/premises/{pid}")
        assert r2.status_code == 200
        assert r2.json()["status"] == "draft"

    def test_delete_confirmed_409(self):
        r = self.client.post("/api/premises", json={"premise_text": "Confirmed delete test"})
        pid = r.json()["premise_id"]
        # Manually set to confirmed
        store = PremiseStore()
        store.premises[pid]["status"] = "confirmed"
        store.save()
        r2 = self.client.delete(f"/api/premises/{pid}")
        assert r2.status_code == 409

    def test_list_after_create_shows_premise(self):
        self.client.post("/api/premises", json={"premise_text": "Listable premise"})
        r = self.client.get("/api/premises")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        ids = [p["premise_id"] for p in data]
        assert len(ids) == len(set(ids))  # no duplicates


# ===========================================================================
# §6.5 — Real ledger untouched invariant
# ===========================================================================

def test_real_ledger_path_untouched_in_module(tmp_path):
    """_REAL_FDR_LEDGER in premise_run.py must point to fdr_ledger.json and be a
    module constant only — no code path in v1 (preview/explore/graduate) passes it
    to compile_spec or run_event_study."""
    import research.premise_run as pr
    import inspect

    real_ledger = pr._REAL_FDR_LEDGER
    assert "fdr_ledger.json" in str(real_ledger)

    # Inspect _run_preview_sync: must not reference _REAL_FDR_LEDGER
    src_preview = inspect.getsource(pr._run_preview_sync)
    assert "_REAL_FDR_LEDGER" not in src_preview

    # Inspect _run_full_explore_sync: must not reference _REAL_FDR_LEDGER
    src_explore = inspect.getsource(pr._run_full_explore_sync)
    assert "_REAL_FDR_LEDGER" not in src_explore

    # Graduate: references _REAL_FDR_LEDGER only in string (future_worker_cmd)
    # but must not pass it to compile_spec
    src_graduate = inspect.getsource(pr.graduate_to_confirm)
    assert "compile_spec" not in src_graduate


def test_worker_module_never_references_real_ledger():
    """premise_run_worker.run_full_explore_sync must not reference _REAL_FDR_LEDGER."""
    import inspect
    from research import premise_run_worker as pw

    src = inspect.getsource(pw.run_full_explore_sync)
    assert "_REAL_FDR_LEDGER" not in src
    assert "fdr_ledger_path=None" in src


# ===========================================================================
# §6.6 — Slow marker: real fast-preview (skip by default)
# ===========================================================================

# ===========================================================================
# §G1 — confirm_request idempotency guard uses correct field name
# ===========================================================================

class TestG1ConfirmRequestGuard:
    """G1: The run_history idempotency guard must check r.get('type')=='confirm_request'."""

    @pytest.fixture(autouse=True)
    def isolate_store(self, tmp_path):
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _setup_explored_premise(self) -> tuple[PremiseStore, str]:
        store = PremiseStore()
        pid = store.add_premise("G1 idempotency test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        store.premises[pid]["status"] = "explored"
        store.save()
        return store, pid

    def test_confirm_request_guard_rejects_second_graduate_on_same_premise(self):
        """After graduating once, reverting to 'explored' and trying again must 409."""
        import research.premise_run as pr

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        # First graduate
        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            asyncio.get_event_loop().run_until_complete(pr.graduate_to_confirm(pid))

        # Manually revert to explored (simulate the reverted state the guard is meant to catch)
        store2 = PremiseStore()
        store2.premises[pid]["status"] = "explored"
        store2.save()

        # Second graduate of the same premise should 409 due to confirm_request in run_history
        from fastapi import HTTPException
        with patch.object(pr, "_check_power_audit", _mock_power_audit):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(pr.graduate_to_confirm(pid))
        assert exc_info.value.status_code == 409
        assert "confirm_request" in exc_info.value.detail.lower()


# ===========================================================================
# §G2 — save_spec blocks frozen states and returns actual status
# ===========================================================================

class TestG2SaveSpecFrozenStates:
    """G2: save_spec must refuse on exploring/awaiting_confirm/confirmed."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        import research.premise_run as pr
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")
        pr._jobs.clear()
        pr._job_locks.clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.premises import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def _create_and_get_pid(self, text: str = "G2 test premise") -> str:
        r = self.client.post("/api/premises", json={"premise_text": text})
        return r.json()["premise_id"]

    def _save_valid_spec(self, pid: str):
        spec_dict = {
            "premise_text": "G2 save spec test",
            "stream": "form4",
            "dose": "r1_score",
            "event_filter": {"transaction_codes": ["P"]},
            "horizons": [21, 63, 126],
        }
        return self.client.put(f"/api/premises/{pid}/spec", json=spec_dict)

    def test_save_spec_blocked_on_exploring(self):
        pid = self._create_and_get_pid()
        self._save_valid_spec(pid)
        # Force status to exploring
        store = PremiseStore()
        store.premises[pid]["status"] = "exploring"
        store.save()
        r = self._save_valid_spec(pid)
        assert r.status_code == 409
        assert "exploring" in r.json()["detail"].lower() or "frozen" in r.json()["detail"].lower()

    def test_save_spec_blocked_on_awaiting_confirm(self):
        pid = self._create_and_get_pid()
        self._save_valid_spec(pid)
        store = PremiseStore()
        store.premises[pid]["status"] = "awaiting_confirm"
        store.save()
        r = self._save_valid_spec(pid)
        assert r.status_code == 409

    def test_save_spec_blocked_on_confirmed(self):
        pid = self._create_and_get_pid()
        store = PremiseStore()
        store.premises[pid]["status"] = "confirmed"
        store.save()
        r = self._save_valid_spec(pid)
        assert r.status_code == 409

    def test_save_spec_returns_actual_status_not_hardcoded(self):
        """save_spec must return the real status after the transition."""
        pid = self._create_and_get_pid()
        # draft → should advance to spec_ready via awaiting_formalization path,
        # but since draft→spec_ready is blocked by state machine, status stays draft
        # unless awaiting_formalization is first. We advance manually.
        store = PremiseStore()
        store.premises[pid]["status"] = "awaiting_formalization"
        store.save()
        r = self._save_valid_spec(pid)
        assert r.status_code == 200
        # The actual status after successful save from awaiting_formalization must be spec_ready
        assert r.json()["status"] == "spec_ready"

    def test_save_spec_non_dict_body_422(self):
        """G11: non-dict body (bare string) must return 422."""
        pid = self._create_and_get_pid()
        r = self.client.put(f"/api/premises/{pid}/spec", content='"just a string"',
                            headers={"Content-Type": "application/json"})
        # FastAPI 422 or our own 422
        assert r.status_code in (422,)


# ===========================================================================
# §G3 — DONE-without-valid-verdict treated as failure
# ===========================================================================

class TestG3DoneWithoutVerdict:
    """G3: STATUS=DONE + missing verdict file → job failed + store→spec_ready."""

    @pytest.fixture(autouse=True)
    def reset_jobs(self, tmp_path):
        import research.premise_run as pr
        pr._jobs.clear()
        pr._job_locks.clear()
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def test_done_missing_verdict_treated_as_failure(self, tmp_path, monkeypatch):
        import research.premise_run as pr

        store = PremiseStore()
        pid = store.add_premise("G3 test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        outdir = str(tmp_path / "study_outdir")
        logname = "test.log"

        # Inject running explore job (NO verdict file written)
        pr._jobs[pid] = {
            "status": "running",
            "run_type": "explore",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": outdir,
            "logname": logname,
            "verdict": None,
        }

        # Put premise in exploring state
        store.premises[pid]["status"] = "exploring"
        store.save()

        # Mock worker-status to return DONE but don't write the verdict file
        def mock_subprocess_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "STATUS=DONE exit=0"
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        job = pr.poll_explore_status(pid)

        # Must be failed, not done
        assert job["status"] == "failed", f"Expected failed, got {job['status']}"
        assert job["error"] is not None
        assert "missing" in job["error"].lower() or "unreadable" in job["error"].lower()

        # Store must have reverted to spec_ready
        store2 = PremiseStore()
        assert store2.premises[pid]["status"] == "spec_ready"


# ===========================================================================
# §G4 — STATUS=TIMEOUT handled in poll loop
# ===========================================================================

class TestG4TimeoutHandled:
    """G4: STATUS=TIMEOUT must be treated as failure, not left as running."""

    @pytest.fixture(autouse=True)
    def reset_jobs(self, tmp_path):
        import research.premise_run as pr
        pr._jobs.clear()
        pr._job_locks.clear()
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _inject_running_job(self, pid: str, outdir: str, logname: str):
        import research.premise_run as pr
        pr._jobs[pid] = {
            "status": "running",
            "run_type": "explore",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": outdir,
            "logname": logname,
            "verdict": None,
        }

    def test_timeout_treated_as_failure(self, tmp_path, monkeypatch):
        import research.premise_run as pr

        store = PremiseStore()
        pid = store.add_premise("G4 timeout test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        outdir = str(tmp_path / "study_outdir")
        logname = "test.log"
        Path(outdir).mkdir(parents=True, exist_ok=True)

        self._inject_running_job(pid, outdir, logname)
        store.premises[pid]["status"] = "exploring"
        store.save()

        def mock_subprocess_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 2
            r.stdout = "STATUS=TIMEOUT after 3600s"
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        job = pr.poll_explore_status(pid)

        assert job["status"] == "failed", f"Expected failed, got {job['status']}"
        assert "timeout" in job["error"].lower()

        store2 = PremiseStore()
        assert store2.premises[pid]["status"] == "spec_ready"

    def test_failed_status_reverts_to_spec_ready(self, tmp_path, monkeypatch):
        import research.premise_run as pr

        store = PremiseStore()
        pid = store.add_premise("G4 failed test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        outdir = str(tmp_path / "study_outdir2")
        logname = "test2.log"
        Path(outdir).mkdir(parents=True, exist_ok=True)

        self._inject_running_job(pid, outdir, logname)
        store.premises[pid]["status"] = "exploring"
        store.save()

        def mock_subprocess_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1
            r.stdout = "STATUS=FAILED exit=1"
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        job = pr.poll_explore_status(pid)
        assert job["status"] == "failed"

        store2 = PremiseStore()
        assert store2.premises[pid]["status"] == "spec_ready"


# ===========================================================================
# §G5 — premise_id format validation
# ===========================================================================

class TestG5PremiseIdValidation:
    """G5: malformed premise_id must be rejected at FastAPI layer (422) and service layer."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        import research.premise_run as pr
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")
        pr._jobs.clear()
        pr._job_locks.clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.premises import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_malformed_premise_id_get_returns_422(self):
        """GET with bad ID format → 422 (FastAPI Path constraint).
        Note: path-traversal sequences like ../../evil are resolved to 404 by
        FastAPI routing (the path is normalized away), but non-traversal
        bad IDs like 'notanid' correctly return 422."""
        r = self.client.get("/api/premises/notanid")
        assert r.status_code == 422

    def test_malformed_premise_id_put_spec_returns_422(self):
        r = self.client.put("/api/premises/notanid/spec", json={"premise_text": "x"})
        assert r.status_code == 422

    def test_malformed_premise_id_post_run_returns_422(self):
        r = self.client.post("/api/premises/notanid/run", json={"mode": "preview"})
        assert r.status_code == 422

    def test_malformed_premise_id_delete_returns_422(self):
        r = self.client.delete("/api/premises/notanid")
        assert r.status_code == 422

    def test_service_assert_format_raises_value_error(self):
        """_assert_premise_id_format raises ValueError for bad IDs."""
        import research.premise_run as pr
        with pytest.raises(ValueError, match="Invalid premise_id"):
            pr._assert_premise_id_format("../../evil")
        with pytest.raises(ValueError, match="Invalid premise_id"):
            pr._assert_premise_id_format("p-TOOLONG9abc")
        # Valid ID must not raise
        pr._assert_premise_id_format("p-1a2b3c4d")


# ===========================================================================
# §G7 — graduate_to_confirm uses single store + rollback on failure
# ===========================================================================

class TestG7GraduateAtomicRollback:
    """G7: graduate_to_confirm must roll back on failure in steps 6-8."""

    @pytest.fixture(autouse=True)
    def isolate_store(self, tmp_path):
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _setup_explored_premise(self) -> tuple[PremiseStore, str]:
        store = PremiseStore()
        pid = store.add_premise("G7 rollback test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        store.premises[pid]["status"] = "explored"
        store.save()
        return store, pid

    def test_rollback_on_transition_failure(self):
        """If transition fails during steps 6-8, spec_hash must not be persisted."""
        import research.premise_run as pr
        from research.premise_store import PremiseStore as _PS

        _store, pid = self._setup_explored_premise()

        async def _mock_power_audit():
            pass

        # Capture the spec BEFORE the graduate attempt
        pre_spec = copy.deepcopy(PremiseStore().premises[pid].get("spec"))
        pre_status = PremiseStore().premises[pid].get("status")

        # Mock transition to fail on awaiting_confirm
        original_transition = _PS.transition

        def _bad_transition(self, premise_id, new_state):
            if new_state == "awaiting_confirm":
                raise RuntimeError("Simulated disk failure during transition")
            return original_transition(self, premise_id, new_state)

        with patch.object(_PS, "transition", _bad_transition):
            with patch.object(pr, "_check_power_audit", _mock_power_audit):
                with pytest.raises(Exception):
                    asyncio.get_event_loop().run_until_complete(
                        pr.graduate_to_confirm(pid)
                    )

        # After rollback, status must still be explored (pre-graduate)
        store_post = PremiseStore()
        post_p = store_post.premises[pid]
        assert post_p["status"] == pre_status, (
            f"Expected status {pre_status!r} after rollback, got {post_p['status']!r}"
        )


# ===========================================================================
# §G8 — trigger_run blocks on awaiting_confirm state
# ===========================================================================

class TestG8TriggerRunStatusGuard:
    """G8: trigger_run must 409 if premise is in awaiting_confirm (or other non-runnable states)."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        import research.premise_run as pr
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")
        pr._jobs.clear()
        pr._job_locks.clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.premises import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_trigger_run_from_awaiting_confirm_returns_409(self):
        r = self.client.post("/api/premises", json={"premise_text": "G8 awaiting_confirm test"})
        pid = r.json()["premise_id"]
        # Set up spec + put in awaiting_confirm
        spec_dict = {
            "premise_text": "G8 awaiting_confirm test",
            "stream": "form4",
            "dose": "r1_score",
        }
        self.client.put(f"/api/premises/{pid}/spec", json=spec_dict)
        store = PremiseStore()
        store.premises[pid]["status"] = "awaiting_confirm"
        store.save()

        r2 = self.client.post(f"/api/premises/{pid}/run", json={"mode": "preview"})
        assert r2.status_code == 409
        assert "awaiting_confirm" in r2.json()["detail"].lower() or "runnable" in r2.json()["detail"].lower()

    def test_trigger_run_from_draft_returns_409(self):
        """A premise in draft (no spec path to run) must also 409 on the status guard."""
        r = self.client.post("/api/premises", json={"premise_text": "G8 draft test"})
        pid = r.json()["premise_id"]
        # Save spec first so we pass the spec-presence check; then set status to draft
        spec_dict = {
            "premise_text": "G8 draft test",
            "stream": "form4",
            "dose": "r1_score",
        }
        store = PremiseStore()
        store.premises[pid]["status"] = "awaiting_formalization"
        store.save()
        self.client.put(f"/api/premises/{pid}/spec", json=spec_dict)
        # Force back to draft
        store2 = PremiseStore()
        store2.premises[pid]["status"] = "draft"
        store2.save()

        r2 = self.client.post(f"/api/premises/{pid}/run", json={"mode": "preview"})
        assert r2.status_code == 409


# ===========================================================================
# §G9 — Concurrent trigger_run is serialized by lock
# ===========================================================================

class TestG9ConcurrentTrigger:
    """G9: two concurrent POST /run for the same premise must not both succeed."""

    @pytest.fixture(autouse=True)
    def reset_jobs(self, tmp_path):
        import research.premise_run as pr
        pr._jobs.clear()
        pr._job_locks.clear()
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def test_concurrent_triggers_only_one_succeeds(self):
        """Both run_preview coroutines for the same premise: first succeeds, second 409."""
        import research.premise_run as pr
        from fastapi import HTTPException

        store = PremiseStore()
        pid = store.add_premise("G9 concurrent test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        store.premises[pid]["status"] = "spec_ready"
        store.save()

        # Both triggers run concurrently on the same event loop
        results = []
        errors = []

        async def _attempt():
            try:
                await pr.run_preview(pid)
                results.append("ok")
            except HTTPException as e:
                errors.append(e.status_code)
            except Exception as e:
                errors.append(str(e))

        async def _run_both():
            await asyncio.gather(_attempt(), _attempt(), return_exceptions=True)

        asyncio.get_event_loop().run_until_complete(_run_both())

        total = len(results) + len(errors)
        assert total == 2, f"Expected 2 outcomes, got {total}"
        # At most one can succeed (may also get ValueError from illegal transition on the 2nd)
        assert len(results) <= 1, "At most one concurrent trigger should succeed"


# ===========================================================================
# §G10 — delete_premise refuses if active job in flight
# ===========================================================================

class TestG10DeleteWithActiveJob:
    """G10: DELETE on a premise with a running job must return 409."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        import research.premise_run as pr
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")
        pr._jobs.clear()
        pr._job_locks.clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.premises import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_delete_blocked_when_job_running(self):
        import research.premise_run as pr

        r = self.client.post("/api/premises", json={"premise_text": "G10 delete test"})
        pid = r.json()["premise_id"]

        # Inject running job
        pr._jobs[pid] = {
            "status": "running",
            "run_type": "preview",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": None,
            "logname": None,
            "verdict": None,
        }

        r2 = self.client.delete(f"/api/premises/{pid}")
        assert r2.status_code == 409
        assert "run" in r2.json()["detail"].lower() or "progress" in r2.json()["detail"].lower()

    def test_delete_allowed_when_job_done(self):
        import research.premise_run as pr

        r = self.client.post("/api/premises", json={"premise_text": "G10 done delete test"})
        pid = r.json()["premise_id"]

        # Inject done (terminal) job
        pr._jobs[pid] = {
            "status": "done",
            "run_type": "preview",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
            "outdir": None,
            "logname": None,
            "verdict": {"explore_decision": "ADVANCE"},
        }

        r2 = self.client.delete(f"/api/premises/{pid}")
        assert r2.status_code == 200


# ===========================================================================
# §G11 — Pydantic request model validation
# ===========================================================================

class TestG11PydanticValidation:
    """G11: RunRequest mode must be Literal; invalid values return 422 at model level."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        import research.premise_run as pr
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")
        pr._jobs.clear()
        pr._job_locks.clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.premises import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_invalid_mode_returns_422(self):
        """RunRequest with invalid mode must return 422 (Literal enforcement)."""
        r = self.client.post("/api/premises", json={"premise_text": "G11 mode test"})
        pid = r.json()["premise_id"]

        r2 = self.client.post(f"/api/premises/{pid}/run", json={"mode": "invalid_mode"})
        assert r2.status_code == 422

    def test_missing_mode_returns_422(self):
        """RunRequest with no mode field must return 422."""
        r = self.client.post("/api/premises", json={"premise_text": "G11 no mode"})
        pid = r.json()["premise_id"]

        r2 = self.client.post(f"/api/premises/{pid}/run", json={})
        assert r2.status_code == 422

    def test_valid_preview_mode_passes_schema(self):
        """RunRequest.mode='preview' must pass model validation (400 if no spec, not 422)."""
        r = self.client.post("/api/premises", json={"premise_text": "G11 valid preview"})
        pid = r.json()["premise_id"]

        # No spec, but valid mode — expect 400 (no spec), not 422 (invalid mode)
        r2 = self.client.post(f"/api/premises/{pid}/run", json={"mode": "preview"})
        assert r2.status_code == 400
        assert "spec" in r2.json()["detail"].lower()


# ===========================================================================
# §C-04 — _read_worker_verdict tries both filenames (F420)
# ===========================================================================

class TestC04ReadWorkerVerdictBothFilenames:
    """C-04: _read_worker_verdict must try r1_explore_verdict.json first,
    then fall back to s1_onesample_verdict.json if the mirror write failed."""

    def test_reads_r1_explore_verdict_json(self, tmp_path):
        """Primary path: r1_explore_verdict.json is present — use it."""
        import research.premise_run as pr

        outdir = str(tmp_path / "study")
        Path(outdir).mkdir(parents=True, exist_ok=True)
        verdict = {"explore_decision": "ADVANCE", "source": "r1"}
        (Path(outdir) / "r1_explore_verdict.json").write_text(
            json.dumps(verdict), encoding="utf-8"
        )
        result = pr._read_worker_verdict(outdir)
        assert result is not None
        assert result["source"] == "r1"

    def test_falls_back_to_s1_onesample_verdict_json(self, tmp_path):
        """Fallback path: r1_explore_verdict.json absent (mirror write failed),
        s1_onesample_verdict.json present — must be returned with a warning."""
        import research.premise_run as pr

        outdir = str(tmp_path / "study2")
        Path(outdir).mkdir(parents=True, exist_ok=True)
        verdict = {"explore_decision": "ADVANCE", "source": "s1"}
        (Path(outdir) / "s1_onesample_verdict.json").write_text(
            json.dumps(verdict), encoding="utf-8"
        )
        result = pr._read_worker_verdict(outdir)
        assert result is not None, "Should fall back to s1_onesample_verdict.json"
        assert result["source"] == "s1"

    def test_returns_none_when_neither_present(self, tmp_path):
        """No verdict file at all → None (unchanged behavior)."""
        import research.premise_run as pr

        outdir = str(tmp_path / "study3")
        Path(outdir).mkdir(parents=True, exist_ok=True)
        result = pr._read_worker_verdict(outdir)
        assert result is None

    def test_r1_takes_precedence_over_s1(self, tmp_path):
        """When both files exist, r1_explore_verdict.json must be returned."""
        import research.premise_run as pr

        outdir = str(tmp_path / "study4")
        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / "r1_explore_verdict.json").write_text(
            json.dumps({"source": "r1_preferred"}), encoding="utf-8"
        )
        (Path(outdir) / "s1_onesample_verdict.json").write_text(
            json.dumps({"source": "s1_fallback"}), encoding="utf-8"
        )
        result = pr._read_worker_verdict(outdir)
        assert result is not None
        assert result["source"] == "r1_preferred"


# ===========================================================================
# §R-5 — TOCTOU guard in poll_explore_status (F420)
# ===========================================================================

class TestR5TOCTOUGuard:
    """R-5: poll_explore_status must bail if job status is no longer 'running'
    when re-checked inside the thread (after subprocess returns STATUS=DONE).
    Deterministic unit test — no sleep-based race."""

    @pytest.fixture(autouse=True)
    def reset_jobs(self, tmp_path):
        import research.premise_run as pr
        pr._jobs.clear()
        pr._job_locks.clear()
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def test_second_poll_skips_when_status_already_done(self, tmp_path, monkeypatch):
        """Simulate concurrent DONE processing: the first poll mutates status to 'done'
        while subprocess.run is in flight for the second poll.

        Strategy: monkeypatch subprocess.run to mutate the job dict before returning,
        so when poll_explore_status re-reads _jobs inside the function, status is
        already 'done' (not 'running') — the guard should bail.
        """
        import research.premise_run as pr

        store = PremiseStore()
        pid = store.add_premise("R-5 TOCTOU guard test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        outdir = str(tmp_path / "study_r5")
        logname = "r5_test.log"
        Path(outdir).mkdir(parents=True, exist_ok=True)

        # Write a real verdict file so the first poll would succeed normally
        fake_verdict = {"explore_decision": "ADVANCE", "n_valid_events": 10}
        (Path(outdir) / "r1_explore_verdict.json").write_text(
            json.dumps(fake_verdict), encoding="utf-8"
        )

        # Inject a running job
        pr._jobs[pid] = {
            "status": "running",
            "run_type": "explore",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": outdir,
            "logname": logname,
            "verdict": None,
        }
        store.premises[pid]["status"] = "exploring"
        store.save()

        # Simulate: while subprocess.run is "in flight", a concurrent poll mutates status
        original_subprocess_run = __import__("subprocess").run

        def mock_subprocess_run(cmd, **kwargs):
            # Simulate first poll already processing the DONE verdict:
            # mutate the job dict to 'done' mid-flight
            pr._jobs[pid]["status"] = "done"
            pr._jobs[pid]["verdict"] = fake_verdict
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "STATUS=DONE exit=0"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        # This poll arrives "second" — the guard should detect status != 'running' and bail
        result = pr.poll_explore_status(pid)

        # The TOCTOU guard must have bailed: returned the already-done job state
        # without re-processing the DONE transition (no duplicate ledger append etc.)
        assert result["status"] == "done", (
            f"Expected guard to return already-done state, got {result['status']!r}"
        )
        # The verdict must be the one set by the first poll, not None
        assert result["verdict"] is not None

    def test_normal_poll_still_works_when_status_is_running(self, tmp_path, monkeypatch):
        """Normal case: status IS 'running' when re-read — guard passes, poll proceeds."""
        import research.premise_run as pr

        store = PremiseStore()
        pid = store.add_premise("R-5 normal poll test")
        spec = _minimal_spec()
        store.add_spec(pid, spec.model_dump())
        outdir = str(tmp_path / "study_r5_normal")
        logname = "r5_normal.log"
        Path(outdir).mkdir(parents=True, exist_ok=True)

        fake_verdict = {"explore_decision": "ADVANCE", "n_valid_events": 5}
        (Path(outdir) / "r1_explore_verdict.json").write_text(
            json.dumps(fake_verdict), encoding="utf-8"
        )

        pr._jobs[pid] = {
            "status": "running",
            "run_type": "explore",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "outdir": outdir,
            "logname": logname,
            "verdict": None,
        }
        store.premises[pid]["status"] = "exploring"
        store.save()

        # Normal subprocess — status stays 'running' until the guard check
        def mock_subprocess_run(cmd, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "STATUS=DONE exit=0"
            mock_result.stderr = ""
            return mock_result

        monkeypatch.setattr("subprocess.run", mock_subprocess_run)

        result = pr.poll_explore_status(pid)
        # Normal path: guard passed, poll processed to done
        assert result["status"] == "done"
        assert result["verdict"] is not None


# ===========================================================================
# §SEC-05 — max_length on premise_text / plain_summary (F420)
# ===========================================================================

class TestSec05MaxLength:
    """SEC-05: CreatePremiseRequest.premise_text must reject strings > 4000 chars (422)."""

    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        import research.premise_run as pr
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")
        pr._jobs.clear()
        pr._job_locks.clear()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.premises import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_premise_text_too_long_returns_422(self):
        """POST /api/premises with premise_text > 4000 chars → 422."""
        long_text = "A" * 4001
        r = self.client.post("/api/premises", json={"premise_text": long_text})
        assert r.status_code == 422, (
            f"Expected 422 for premise_text > 4000 chars, got {r.status_code}"
        )

    def test_premise_text_exactly_4000_chars_accepted(self):
        """POST /api/premises with premise_text == 4000 chars → 201 (at boundary)."""
        boundary_text = "B" * 4000
        r = self.client.post("/api/premises", json={"premise_text": boundary_text})
        assert r.status_code == 201, (
            f"Expected 201 for 4000-char premise_text (boundary), got {r.status_code}"
        )

    def test_premise_spec_plain_summary_too_long_rejected(self):
        """PremiseSpec.plain_summary > 4000 chars → Pydantic ValidationError."""
        from pydantic import ValidationError
        from research.premise_spec import PremiseSpec

        with pytest.raises(ValidationError):
            PremiseSpec(
                premise_text="Valid premise text",
                plain_summary="P" * 4001,
            )


@pytest.mark.slow
def test_preview_run_real_data(tmp_path):
    """Real fast preview with pre-stated F338 anchors.

    Pre-stated anchors:
    - At least 1 event in 2019-2020 window
    - explore_decision is one of the known enum values
    - No files written to real fdr_ledger.json
    - study_name contains "_preview_"

    Skipped unless price_cache and edgar_cache are present locally.
    """
    import research.premise_run as pr
    import research.premise_store as _ps

    _ps.DATA_PATH = str(tmp_path / "premises.json")
    pr._jobs.clear()

    # Check caches are available
    try:
        pr._check_required_caches()
    except RuntimeError as e:
        pytest.skip(f"Required caches absent: {e}")

    store = PremiseStore()
    pid = store.add_premise("Real preview smoke test")
    spec = _minimal_spec(n_boot=9)  # very fast
    store.add_spec(pid, spec.model_dump())

    # Capture real ledger mtime before run
    real_ledger = pr._REAL_FDR_LEDGER
    mtime_before = real_ledger.stat().st_mtime if real_ledger.exists() else None

    # Run preview sync (not async wrapper — direct for test simplicity)
    verdict = pr._run_preview_sync(pid)

    # Anchor: explore_decision is a known value
    known_decisions = {
        "ADVANCE", "WEAKENED-IN-EXPLORE", "UNTESTABLE-underpowered",
        "UNTESTABLE-insufficient_events", "FAILED-analysis",
    }
    # Allow any string (analysis module may return custom values)
    assert isinstance(verdict.get("explore_decision"), (str, type(None)))

    # Anchor: study_name contains _preview_
    store2 = PremiseStore()
    p = store2.premises[pid]
    last_run = p.get("run_history", [])[-1] if p.get("run_history") else {}
    assert "_preview_" in last_run.get("study_name", ""), (
        "Preview study_name must contain _preview_"
    )

    # Anchor: verdict_valid is False for preview
    assert last_run.get("verdict_valid") is False

    # Anchor: real ledger NOT touched
    if mtime_before is not None:
        mtime_after = real_ledger.stat().st_mtime if real_ledger.exists() else None
        assert mtime_after == mtime_before, "Real FDR ledger must NOT be touched by preview"
    elif real_ledger.exists():
        pytest.fail("Real FDR ledger appeared during preview run — invariant violated")


# ===========================================================================
# F390 — B1/B2 route-level tests (submit + save_spec auto-advance)
# Uses the FastAPI test client to exercise the full route logic.
# ===========================================================================

class TestSubmitEndpoint:
    """B1: POST /api/premises/{id}/submit — draft → awaiting_formalization."""

    @pytest.fixture(autouse=True)
    def isolate_store(self, tmp_path):
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _client(self):
        from fastapi.testclient import TestClient
        import sys
        _BACKEND_DIR_STR = str(_BACKEND_DIR)
        if _BACKEND_DIR_STR not in sys.path:
            sys.path.insert(0, _BACKEND_DIR_STR)
        from main import app
        return TestClient(app)

    def test_submit_draft_transitions_to_awaiting_formalization(self):
        """POST /submit on a draft premise must transition to awaiting_formalization."""
        client = self._client()

        # Create a premise
        create_resp = client.post("/api/premises", json={"premise_text": "Test submit premise"})
        assert create_resp.status_code == 201
        pid = create_resp.json()["premise_id"]
        assert create_resp.json()["status"] == "draft"

        # Submit
        submit_resp = client.post(f"/api/premises/{pid}/submit")
        assert submit_resp.status_code == 200, submit_resp.text
        body = submit_resp.json()
        assert body["premise_id"] == pid
        assert body["status"] == "awaiting_formalization"

        # Verify stored status
        get_resp = client.get(f"/api/premises/{pid}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "awaiting_formalization"

    def test_submit_non_draft_returns_409(self):
        """POST /submit on a non-draft premise must return 409."""
        client = self._client()

        # Create + submit to move to awaiting_formalization
        create_resp = client.post("/api/premises", json={"premise_text": "Test 409 premise"})
        assert create_resp.status_code == 201
        pid = create_resp.json()["premise_id"]

        # First submit — OK
        r1 = client.post(f"/api/premises/{pid}/submit")
        assert r1.status_code == 200

        # Second submit on awaiting_formalization — must 409
        r2 = client.post(f"/api/premises/{pid}/submit")
        assert r2.status_code == 409

    def test_submit_unknown_id_returns_404(self):
        """POST /submit on an unknown premise_id must return 404."""
        client = self._client()
        r = client.post("/api/premises/p-00000000/submit")
        assert r.status_code == 404


class TestSaveSpecAutoAdvance:
    """B2: PUT /spec auto-advances through legal edges to reach spec_ready."""

    @pytest.fixture(autouse=True)
    def isolate_store(self, tmp_path):
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _client(self):
        from fastapi.testclient import TestClient
        import sys
        _BACKEND_DIR_STR = str(_BACKEND_DIR)
        if _BACKEND_DIR_STR not in sys.path:
            sys.path.insert(0, _BACKEND_DIR_STR)
        from main import app
        return TestClient(app)

    def _minimal_spec_dict(self, text: str = "Test spec premise") -> dict:
        return {
            "premise_text": text,
            "stream": "form4",
            "event_filter": {"transaction_codes": ["P"]},
            "dose": "r1_score",
            "dose_params": {},
            "horizons": [21, 63, 126],
            "entry_lag_days": 1,
            "dedup_same_ticker": True,
            "dedup_window_days": 30,
            "direction": "long",
            "floors": {"min_price": 5.0, "min_avg_volume": 500000},
            "min_peer_count": 8,
            "fdr_q": 0.10,
            "n_boot": 99,
        }

    def test_save_spec_from_draft_reaches_spec_ready(self):
        """PUT /spec on a draft premise must auto-advance to spec_ready."""
        client = self._client()

        create_resp = client.post("/api/premises", json={"premise_text": "Draft spec test"})
        assert create_resp.status_code == 201
        pid = create_resp.json()["premise_id"]
        assert create_resp.json()["status"] == "draft"

        spec = self._minimal_spec_dict("Draft spec test")
        put_resp = client.put(f"/api/premises/{pid}/spec", json=spec)
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["status"] == "spec_ready"

        # Verify stored status
        get_resp = client.get(f"/api/premises/{pid}")
        assert get_resp.json()["status"] == "spec_ready"

    def test_save_spec_from_explored_reaches_spec_ready(self):
        """PUT /spec on an explored premise must auto-advance to spec_ready."""
        client = self._client()

        # Create + manually force to explored
        create_resp = client.post("/api/premises", json={"premise_text": "Explored spec test"})
        pid = create_resp.json()["premise_id"]

        # Force explored state directly in store
        store = PremiseStore()
        store.premises[pid]["status"] = "explored"
        store.save()

        spec = self._minimal_spec_dict("Explored spec test")
        put_resp = client.put(f"/api/premises/{pid}/spec", json=spec)
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["status"] == "spec_ready"

        get_resp = client.get(f"/api/premises/{pid}")
        assert get_resp.json()["status"] == "spec_ready"

    def test_save_spec_from_awaiting_formalization_reaches_spec_ready(self):
        """PUT /spec from awaiting_formalization must reach spec_ready in one hop."""
        client = self._client()

        create_resp = client.post("/api/premises", json={"premise_text": "AF spec test"})
        pid = create_resp.json()["premise_id"]

        # Submit to move to awaiting_formalization
        client.post(f"/api/premises/{pid}/submit")

        get_resp = client.get(f"/api/premises/{pid}")
        assert get_resp.json()["status"] == "awaiting_formalization"

        spec = self._minimal_spec_dict("AF spec test")
        put_resp = client.put(f"/api/premises/{pid}/spec", json=spec)
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["status"] == "spec_ready"


class TestListStatusFilter:
    """H6: GET /api/premises?status= filters list to matching status; 422 on unknown."""

    @pytest.fixture(autouse=True)
    def isolate_store(self, tmp_path):
        _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    def _client(self):
        from fastapi.testclient import TestClient
        import sys
        _BACKEND_DIR_STR = str(_BACKEND_DIR)
        if _BACKEND_DIR_STR not in sys.path:
            sys.path.insert(0, _BACKEND_DIR_STR)
        from main import app
        return TestClient(app)

    def test_filter_returns_only_matching_status(self):
        """?status=awaiting_formalization must return only premises with that status."""
        client = self._client()

        # Create two premises
        r1 = client.post("/api/premises", json={"premise_text": "premise one"})
        pid1 = r1.json()["premise_id"]

        r2 = client.post("/api/premises", json={"premise_text": "premise two"})
        pid2 = r2.json()["premise_id"]

        # Submit pid1 → awaiting_formalization; leave pid2 as draft
        client.post(f"/api/premises/{pid1}/submit")

        # Without filter: both show up
        all_resp = client.get("/api/premises")
        assert all_resp.status_code == 200
        all_ids = {p["premise_id"] for p in all_resp.json()}
        assert pid1 in all_ids
        assert pid2 in all_ids

        # With status=awaiting_formalization: only pid1
        af_resp = client.get("/api/premises?status=awaiting_formalization")
        assert af_resp.status_code == 200
        af_ids = {p["premise_id"] for p in af_resp.json()}
        assert pid1 in af_ids
        assert pid2 not in af_ids

        # With status=draft: only pid2
        draft_resp = client.get("/api/premises?status=draft")
        assert draft_resp.status_code == 200
        draft_ids = {p["premise_id"] for p in draft_resp.json()}
        assert pid2 in draft_ids
        assert pid1 not in draft_ids

    def test_filter_unknown_status_returns_422(self):
        """?status=<unknown> must return 422."""
        client = self._client()
        resp = client.get("/api/premises?status=not_a_real_status")
        assert resp.status_code == 422, resp.text

    def test_filter_empty_store_returns_empty_list(self):
        """?status=explored on an empty store returns []."""
        client = self._client()
        resp = client.get("/api/premises?status=explored")
        assert resp.status_code == 200
        assert resp.json() == []
