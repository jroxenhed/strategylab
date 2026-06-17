# Desk-Agent Operator Runbook — Premise Workbench (F389)

**Audience:** An attended Claude Code session acting as option-A operator: reading pending premises, formalizing specs, triggering runs, interpreting verdicts, and deciding whether to graduate to confirm.

**What this is not:** A free coder. The operator is a bounded charter co-author. Spec output is vocabulary-validated — bad specs are rejected with 422, not silently accepted.

---

## 1. Session Startup

Find pending work:

```bash
# List premises waiting for spec formalization
curl -s http://localhost:8000/api/premises | python3 -m json.tool | grep -A4 '"awaiting_formalization"'

# Or list all — scan for status fields
curl -s http://localhost:8000/api/premises | python3 -m json.tool
```

States in the workbench lifecycle:

| Status | Meaning | Action |
|--------|---------|--------|
| `draft` | Created, awaiting formalization trigger | Transition to `awaiting_formalization` if text is present |
| `awaiting_formalization` | Raw idea text; needs a validated PremiseSpec | **Formalize it (this session's job)** |
| `spec_ready` | Spec written and validated | Trigger a run |
| `exploring` | Run in flight | Poll run-status |
| `explored` | Run complete, verdict available | Interpret verdict; decide to graduate |
| `awaiting_confirm` | Frozen + queued for real OOS confirm | No further action in v1; F393 deferred |
| `confirmed` | Real OOS confirm run complete (F393) | Terminal — do not modify |

---

## 2. Formalization Flow

### Read the premise

```bash
curl -s http://localhost:8000/api/premises/<premise_id> | python3 -m json.tool
```

Read `premise_text` (the plain-English idea) and any `guided` fields (trigger, stronger_when, hold_length, direction). These are your inputs.

### Produce a PremiseSpec dict

The spec is a JSON object with these fields. All fields have defaults; only override what the premise idea requires.

```json
{
  "premise_text": "<copy verbatim from premise>",
  "stream": "form4",
  "event_filter": {},
  "dose": "r1_score",
  "dose_params": {},
  "horizons": [21, 63, 126],
  "entry_lag_days": 1,
  "dedup_same_ticker": true,
  "dedup_window_days": 30,
  "direction": "long",
  "floors": {"min_price": 5.0, "min_avg_volume": 500000},
  "min_peer_count": 8,
  "fdr_q": 0.1,
  "n_boot": 999,
  "plain_summary": "<3-5 sentence plain-English readback of what this test does>"
}
```

Valid `event_filter` keys (form4 stream vocabulary):
- `transaction_codes` — list e.g. `["P", "A"]` (P=open-market purchase, A=grant)
- `exclude_10b51` — bool: exclude 10b5-1 pre-planned trades

Valid `dose_params` keys: none yet (r1_score has no free params in v1).

### Submit the spec

```bash
curl -s -X PUT http://localhost:8000/api/premises/<premise_id>/spec \
  -H "Content-Type: application/json" \
  -d '<spec_json>'
```

**If 422:** Read the validation error. Common fixes:
- `stream` not in registered registry → only `"form4"` is registered
- `event_filter` key not in `filter_vocabulary()` → check valid keys above
- `dose` not in `_VALID_DOSES` → only `"r1_score"` is valid

Retry until 200. The premise transitions to `spec_ready` on success.

---

## 3. Triggering a Run

### Fast preview (seconds-to-minutes)

```bash
curl -s -X POST http://localhost:8000/api/premises/<premise_id>/run \
  -H "Content-Type: application/json" \
  -d '{"mode": "preview"}'
```

Preview scope: 2019-2020 window, event tickers only (no background universe), n_boot=99.
**Note:** universe excess not computed in preview. Not a verdict. Use for quick sanity check.

### Full explore (~76 min on worker)

```bash
curl -s -X POST http://localhost:8000/api/premises/<premise_id>/run \
  -H "Content-Type: application/json" \
  -d '{"mode": "explore"}'
```

Dispatches to worker via `bin/worker-dispatch.sh`. Returns immediately.
Poll status with:

```bash
# Poll until done (run in a loop)
curl -s http://localhost:8000/api/premises/<premise_id>/run-status | python3 -m json.tool
```

Status values: `running` | `done` | `failed` | `unknown` (server restart).

If `"status": "unknown"`: the server restarted mid-run. The worker job may still be running on the remote machine. Check the outdir on the worker:

```bash
tail -f <outdir>/run.log
```

---

## 4. Reading a Verdict

```bash
curl -s http://localhost:8000/api/premises/<premise_id>/verdict | python3 -m json.tool
```

Key fields to interpret (all defined in terms the reader already has):

| Field | Plain meaning |
|-------|--------------|
| `explore_decision` | `"ADVANCE"` = signal found, worth investigating further. `"WEAKENED-IN-EXPLORE"` = statistically significant but not in the expected direction or attenuating. `"UNTESTABLE-underpowered"` = not enough events to test. Other strings indicate specific failure modes. |
| `n_valid_events` | How many filing events were actually used in the analysis (after dedup, floor filters, etc.) |
| `mde_q5q1_pp` | Minimum detectable effect (in percentage points) the test has 80% power to detect. If this is larger than your expected effect size, the test can't reliably find the signal even if it's real. |
| `mde_gate_passed` | Whether the event count clears the power floor. If false, the result is unreliable. |
| `H1.obs_gap_q5q1_pp` | The observed spread (in pp) between the top quintile (highest r1-score insiders) and bottom quintile in forward returns. This is the primary signal estimate. |
| `H1.ci_low_95` / `ci_high_95` | 95% confidence interval on that spread. If it crosses zero, the signal is not statistically significant. |
| `H1.p_boot` | Bootstrap p-value for H1. Below 0.05 is nominally significant. |
| `H1.bh_rejected` | Whether H1 passes the BH false-discovery-rate correction (the program-level multiplicity test). |
| `fdr_report` | Summary of all hypotheses tested and their BH-corrected p-values. |
| `era_lens` | How the signal looks in different time periods (e.g. pre-2016, 2016-2018, 2019-2020). |
| `peer_lens` | Same-SIC-sector peer comparison. |
| `regime_lens` | Signal split by market regime (RISK_ON, NEUTRAL, RISK_OFF, STRESS). |
| `perturbation_band` | Robustness: range of estimates under small parameter perturbations. Narrow band = robust. |

### Plain-language verdict checklist (every verdict must answer all 5)

1. **What stocks?** Which companies are in the signal? (e.g. "all Form 4 open-market purchases by insiders at US equities with ≥500k avg daily volume")
2. **What rule?** What filter / dose was applied? (e.g. "top 20% by r1_score — a composite of insider buying intensity, cluster size, and dollar amount")
3. **What did we measure?** What comparison is the test making? (e.g. "21-day forward excess return: top quintile vs bottom quintile, relative to the liquid universe median")
4. **What was the number?** State the observed gap, CI, and p-value in plain terms. (e.g. "Top quintile beat bottom quintile by +2.1pp over 21 days (95% CI: −0.4pp to +4.6pp), p=0.09 — directionally positive but not BH-significant")
5. **What does it mean for next steps?** Should we graduate? Stop? Adjust the spec? (e.g. "Signal is directionally right but CI crosses zero and BH not rejected → explore_decision=WEAKENED-IN-EXPLORE. Spec adjustment or larger window needed before graduating.")

Write this plain-language verdict to the premise's `plain_summary` field:

```bash
# Update spec with plain_summary (builds on the existing spec)
curl -s http://localhost:8000/api/premises/<premise_id> | python3 -m json.tool > /tmp/p.json
# Edit plain_summary in /tmp/p.json, then:
curl -s -X PUT http://localhost:8000/api/premises/<premise_id>/spec \
  -H "Content-Type: application/json" \
  -d "$(cat /tmp/p.json | python3 -c 'import json,sys; p=json.load(sys.stdin); print(json.dumps(p["spec"]))')"
```

---

## 5. Graduate-to-Confirm Gate

Only graduate when `explore_decision == "ADVANCE"` and you are confident the spec is final.

**Checklist before graduating:**
- [ ] `explore_decision` is `"ADVANCE"`
- [ ] `mde_gate_passed` is true
- [ ] `H1.bh_rejected` is true (BH correction passed at fdr_q=0.10)
- [ ] The plain-language verdict answers all 5 questions
- [ ] The spec `plain_summary` is written and reflects the verdict
- [ ] The spec is final — no further parameter changes planned

```bash
curl -s -X POST http://localhost:8000/api/premises/<premise_id>/graduate-to-confirm \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Success response:** Contains `"status": "awaiting_confirm"` and a `confirm_request` record with the frozen spec + the exact worker command a future real OOS run would use.

**Error responses:**
- `409`: status not `explored`, duplicate spec hash, or already confirmed
- `400`: power audit failed (programme-level MDE check)

### IMPORTANT: What graduate-to-confirm does NOT do in v1

Graduate-to-confirm in v1:
- Freezes the spec (sets `spec_hash`)
- Runs a power audit (programme-level gate, not per-premise)
- Transitions the premise to `awaiting_confirm`
- Records the future OOS run command

It does **NOT**:
- Run any backtest or event study
- Write anything to `fdr_ledger.json`
- Transition the premise to `confirmed`

**The real out-of-sample confirm run (2021+ window, FDR-ledger append) is F393.**
It requires John's methodology sign-off on the confirm window before it can run.
Do not attempt to manually trigger it or write to the ledger.

---

## 6. What to Never Do

- **Never manually write to `fdr_ledger.json`** — that file is a programme-level multiplicity ledger. Only F393 (the real OOS confirm) may append to it, and only after John's sign-off.
- **Never pre-set `spec_hash` in a spec you write** — `spec_hash` is set automatically at confirm-freeze. If you set it, `add_spec` will store your value but `graduate_to_confirm` will overwrite it with the computed hash. Just leave it `null`.
- **Never hand-write a JSON spec directly to the store file** — always go through `PUT /api/premises/{id}/spec` so validation runs.
- **Never skip the validate-spec step** — an unvalidated spec silently inherits wrong vocabulary defaults and produces uninterpretable results.
- **Never interpret a preview verdict as a real verdict** — preview uses 2019-2020 only, event tickers only, n_boot=99. It's a sanity check, not a study result.

---

## 7. Endpoint Reference

| Method | Path | Body | Success | Notes |
|--------|------|------|---------|-------|
| GET | `/api/premises` | — | 200 list | id, status, text excerpt, dates |
| POST | `/api/premises` | `{premise_text}` | 201 `{premise_id, status}` | Creates in 'draft' |
| GET | `/api/premises/{id}` | — | 200 full dict | spec, run_history, error_note |
| PUT | `/api/premises/{id}/spec` | spec dict | 200 `{premise_id, status}` | 422 on validation failure |
| POST | `/api/premises/{id}/run` | `{mode}` | 200 `{status, run_type}` | mode: preview or explore |
| GET | `/api/premises/{id}/run-status` | — | 200 `{status, verdict, ...}` | Poll until done/failed |
| GET | `/api/premises/{id}/verdict` | — | 200 `{verdict}` | Latest verdict from run_history |
| POST | `/api/premises/{id}/graduate-to-confirm` | — | 200 `{status, message, ...}` | 400/409 on failure |
| DELETE | `/api/premises/{id}` | — | 200 `{status: draft}` | Soft-delete; 409 on confirmed |
