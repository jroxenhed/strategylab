"""R-1 Explore Analysis — Agent B.

Reads events.ndjson + meta.json written by the event_study harness, performs the
dose-response quintile analysis, runs H1/H1b/H2 tests, perturbation band, lenses,
and emits r1_explore_verdict.json + a ledger entry.

Charter reference: docs/plans/2026-06-06-R1-insider-cluster-charter-DRAFT.md
Frozen constants are tagged with their charter section (§).

Interface:
    run_r1_analysis(study_dir: Path, *, seed=20260606, ledger_path=None) -> dict
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Charter-frozen constants (§2b / §3a / §3b / §4 / §5)
# ---------------------------------------------------------------------------

# § 3a: primary horizon for the dose-response gate and verdict
PRIMARY_HORIZON: int = 63  # trading days  # Charter default; may be overridden per-call via primary_horizon param (F410)

# § 4: All three horizons
ALL_HORIZONS: tuple[int, ...] = (21, 63, 126)  # Charter default; may be overridden per-call via horizons param (F410)

# § 3a: MDE abort threshold (smallest economically meaningful 63td Q5-Q1 gap)
MDE_ABORT_PP: float = 1.0  # percentage points

# § 5: FDR q-level
FDR_Q: float = 0.10

# § 4 / brief: number of quintiles
N_QUINTILES: int = 5

# § 4: minimum valid-event count in a bootstrap (avoid degenerate p=0/1)
# brief + charter §5
N_BOOT: int = 999

# § 2b / charter: SEED
SEED: int = 20260606

# § 4 / brief: the regime cell that is NEVER evidential (always reported but
# non-evidential).
# §10 PRE-OUTCOME MECHANICS FIX (2026-06-06, caught by the F338 probe before any
# headline was read): the charter §2e/§4 names "STRESS" as the ~6-days-per-decade
# crash state that can never carry evidence.  Counted from the real classifier
# artifact (regime_states.json, 2015-2020): RISK_OFF = 3 days / 6 years (0.2%)
# — THAT is the charter's "6 days/decade" state; STRESS = 11.1% of days.  The
# charter's intent (the vanishingly-rare crisis state is never load-bearing)
# binds to the real rare state, RISK_OFF; its label was wrong (HANDOFF had
# already recorded the F350 label discrepancy).  Evidential trio is therefore
# {RISK_ON, NEUTRAL, STRESS}, each still gated by REGIME_EVIDENTIAL_MIN.
RARE_NON_EVIDENTIAL_STATE: str = "RISK_OFF"
STRESS_STATE: str = RARE_NON_EVIDENTIAL_STATE  # back-compat alias (do not use in new code)

# § 4: minimum events per regime cell to be evidential for REGIME-CARRIED
REGIME_EVIDENTIAL_MIN: int = 15  # §2e / §4 / §9

# § 3b: perturbation band payload keys (W∈{20,21,22} × F∈{0,40k,60k}); primary = W21_F0
PERTURB_KEYS: list[str] = [
    f"W{w}_F{f}"
    for w in (20, 21, 22)
    for f in ("0", "40k", "60k")
]
PRIMARY_PERTURB_KEY: str = "W21_F0"  # § 3b: this IS the primary score

# § 9 / brief: explore decision thresholds (all charter-frozen, encode as constants)
# gap > 0 AND rho_s > 0 AND band_sign_stable AND MDE <= 1.0pp
ADVANCE_GAP_GT: float = 0.0          # § 4 H1: Q5-Q1 gap must be strictly positive
ADVANCE_RHO_GT: float = 0.0          # § 4 H1b: Spearman ρ_s must be strictly positive
ADVANCE_MDE_LE: float = MDE_ABORT_PP  # § 3a: MDE must be <= 1.0pp

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_events(study_dir: Path) -> tuple[list[dict], dict]:
    """Load events.ndjson + meta.json from study_dir."""
    events_path = study_dir / "events.ndjson"
    meta_path = study_dir / "meta.json"
    rows: list[dict] = []
    with events_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    meta: dict = json.loads(meta_path.read_text(encoding="utf-8"))
    return rows, meta


def _is_valid_event(row: dict, primary_horizon: int = PRIMARY_HORIZON) -> bool:
    """Valid for primary analysis: explore split, non-null {primary_horizon}td universe excess, score not None.

    Brief: 'Valid event: explore row, non-null primary-horizon universe excess, score not None.'
    Missing score is treated as score_undefined (brief §score_undefined note).
    primary_horizon defaults to PRIMARY_HORIZON (63) for R-1 charter bit-identity (F410).
    """
    if row.get("split") != "explore":
        return False
    # Primary-horizon universe excess must be non-null
    excess_map = row.get("fwd_excess_pct") or {}
    if excess_map.get(str(primary_horizon)) is None and excess_map.get(primary_horizon) is None:
        return False
    # score must not be None (and the payload might not have it — treat missing as undefined)
    payload = row.get("payload") or {}
    score = payload.get("score")
    if score is None:
        return False
    return True


def _get_excess(row: dict, horizon: int) -> Optional[float]:
    excess_map = row.get("fwd_excess_pct") or {}
    v = excess_map.get(str(horizon))
    if v is None:
        v = excess_map.get(horizon)
    return float(v) if v is not None else None


def _get_peer_excess(row: dict, horizon: int) -> Optional[float]:
    peer_map = row.get("fwd_peer_excess_pct") or {}
    v = peer_map.get(str(horizon))
    if v is None:
        v = peer_map.get(horizon)
    return float(v) if v is not None else None


def _get_entry_year(row: dict) -> Optional[int]:
    entry_date_str = row.get("entry_date")
    if not entry_date_str:
        return None
    try:
        return int(str(entry_date_str)[:4])
    except Exception:
        return None


def _get_score(row: dict) -> Optional[float]:
    payload = row.get("payload") or {}
    s = payload.get("score")
    return float(s) if s is not None else None


def _get_perturb_score(row: dict, key: str) -> Optional[float]:
    """Return perturbed score for key, or None if missing/None (excluded from that variant)."""
    payload = row.get("payload") or {}
    perturb = payload.get("score_perturb") or {}
    v = perturb.get(key)
    return float(v) if v is not None else None


def _assign_quintiles_for_year(rows: list[dict], score_fn) -> dict[int, int]:
    """Assign quintile labels 1..5 to row indices within one year.

    Stable sort by (score, ticker, entry_date) — brief §quintile construction.
    Uses numpy array_split semantics (±1 counts).

    Returns {row_idx_in_input: quintile_label (1..5)}.
    Years with <5 valid events: quintiles can have 1 or 0 members; still assigned.
    """
    scored: list[tuple] = []
    for i, row in enumerate(rows):
        s = score_fn(row)
        if s is None:
            continue
        ticker = row.get("ticker", "")
        entry_date_str = row.get("entry_date", "")
        scored.append((s, ticker, entry_date_str, i))

    # stable sort: (score, ticker, entry_date)
    scored.sort(key=lambda x: (x[0], x[1], x[2]))

    n = len(scored)
    assignments: dict[int, int] = {}
    if n == 0:
        return assignments

    # numpy array_split: splits n items into N_QUINTILES parts, ±1 item
    indices = np.array_split(np.arange(n), N_QUINTILES)
    for q_idx, idx_arr in enumerate(indices):
        q_label = q_idx + 1  # 1..5
        for pos in idx_arr:
            original_idx = scored[int(pos)][3]
            assignments[original_idx] = q_label
    return assignments


def _build_year_groups(valid_rows: list[dict]) -> dict[int, list[int]]:
    """Group valid_rows indices by calendar year (shared structure for perturbation band).

    Returns {year: [row_indices]} — built once and reused across all variant keys.
    """
    year_groups: dict[int, list[int]] = {}
    for i, row in enumerate(valid_rows):
        year = _get_entry_year(row)
        if year is None:
            continue
        year_groups.setdefault(year, []).append(i)
    return year_groups


def _assign_quintiles_all_years_from_groups(
    valid_rows: list[dict],
    year_groups: dict[int, list[int]],
    score_fn,
) -> list[Optional[int]]:
    """Assign quintiles using a pre-built year_groups mapping.

    PERF-06: avoids rebuilding year_groups for each of the 9 perturbation keys.
    """
    quintiles: list[Optional[int]] = [None] * len(valid_rows)
    for year, idxs in year_groups.items():
        year_rows = [valid_rows[i] for i in idxs]
        year_assignments = _assign_quintiles_for_year(year_rows, score_fn)
        for local_i, q_label in year_assignments.items():
            quintiles[idxs[local_i]] = q_label
    return quintiles


def _assign_quintiles_all_years(valid_rows: list[dict], score_fn=None) -> list[Optional[int]]:
    """Assign quintile (1..5) to each valid row, within each entry_date calendar year.

    Brief: 'within each entry_date calendar year, stable-sort by (score, ticker, entry_date),
    equal-count 5-way split (±1), Q1 lowest…Q5 highest.'

    Returns list of length len(valid_rows), quintile label or None if unassignable.
    """
    if score_fn is None:
        score_fn = _get_score

    year_groups = _build_year_groups(valid_rows)
    return _assign_quintiles_all_years_from_groups(valid_rows, year_groups, score_fn)


def _spearman_exact_onesided(x: np.ndarray, y: np.ndarray) -> tuple[Optional[float], float]:
    """Spearman ρ_s + exact one-sided p (H_a: ρ_s > 0) via all 120 permutations of 5 items.

    Brief: 'Spearman H1b is over n=5 points (quintile index vs per-quintile mean)
    — use EXACT permutation (all 120 orderings) for the one-sided p, NOT scipy's asymptotic p.'

    x must have length 5. y is the per-quintile mean excess.
    Returns (rho_s, p_one_sided).

    ADV-01: When all quintile means are identical (constant y), spearmanr returns nan.
    In that case return (None, 1.0) — degenerate, no evidential content.
    """
    assert len(x) == len(y) == N_QUINTILES, f"Expected {N_QUINTILES} points, got {len(x)}"
    from scipy.stats import spearmanr

    # ADV-01: guard against constant y (would yield nan rho and spurious p=0)
    if np.std(y) == 0:
        return None, 1.0

    # Compute observed ρ_s
    rho_obs, _ = spearmanr(x, y)

    # ADV-01: guard against nan rho from scipy (defensive)
    if rho_obs is None or (isinstance(rho_obs, float) and math.isnan(rho_obs)):
        return None, 1.0

    # Exact permutation: all 120 orderings of 5 elements
    count_ge = 0
    total = 0
    for perm_y in itertools.permutations(y):
        rho_perm, _ = spearmanr(x, list(perm_y))
        if rho_perm >= rho_obs:
            count_ge += 1
        total += 1
    p_one_sided = count_ge / total
    return float(rho_obs), p_one_sided


def _two_sample_mbb_bootstrap(
    a: np.ndarray,
    b: np.ndarray,
    block_size_a: int,
    block_size_b: int,
    n_boot: int,
    rng: np.random.Generator,
) -> tuple[float, np.ndarray, int, bool, int, bool]:
    """Two-sample MBB bootstrap for H1: Q5 - Q1 mean difference.

    Brief:
    - Shift BOTH quintile samples to a common mean for H0
    - One-sided p (positive side): frac(boot_diffs_shifted >= obs_diff) — COR-01
    - Percentile CI on UNSHIFTED resampled diffs.
    - COR-03: grand mean is weighted pooled mean.
    - ADV-03: returns None p when min(n)<2.
    - ADV-05: exposes actual block lengths used + capped flags.

    Returns (p_value, boot_diffs_unshifted, L_a_used, capped_a, L_b_used, capped_b).
    p_value is float('nan') when either quintile has n<2.

    PERF-07: Per-iteration vectorization — rng.integers(size=k) is stream-equivalent
    to k sequential scalar calls (verified empirically), so each resample's while-loop
    draws are replaced by a single integers(size=ceil(n/L)) call.  The per-iteration
    interleaving of the four resample slots (shifted_a, shifted_b, raw_a, raw_b) is
    preserved exactly, keeping the RNG stream bit-identical to the original.
    Full cross-iteration batching is NOT used because it would reorder the stream.
    """
    n_a, n_b = len(a), len(b)

    # ADV-03: degenerate — cannot bootstrap with n<2
    if n_a < 2 or n_b < 2:
        # Return nan p; capped flags irrelevant
        return float("nan"), np.empty(0), 1, False, 1, False

    obs_mean_a = float(np.mean(a))
    obs_mean_b = float(np.mean(b))
    obs_diff = obs_mean_a - obs_mean_b  # Q5 - Q1

    # COR-03: weighted pooled grand mean
    grand_mean = (n_a * obs_mean_a + n_b * obs_mean_b) / (n_a + n_b)
    shifted_a = a - obs_mean_a + grand_mean
    shifted_b = b - obs_mean_b + grand_mean

    def _mbb_resample(arr: np.ndarray, block_size: int) -> tuple[np.ndarray, int, bool]:
        n = len(arr)
        if n == 0:
            return arr.copy(), 1, False
        L_requested = block_size
        L = max(1, min(block_size, max(n // 2, 1)))
        capped = L < L_requested
        blocks = []
        collected = 0
        while collected < n:
            start = int(rng.integers(0, n - L + 1))
            blocks.append(arr[start: start + L])
            collected += L
        return np.concatenate(blocks)[:n], L, capped

    # Determine actual L used (from first resample call on shifted arrays)
    _, L_a_used, capped_a = _mbb_resample(shifted_a, block_size_a)
    _, L_b_used, capped_b = _mbb_resample(shifted_b, block_size_b)

    # Pre-compute block counts (ceil(n/L)) and bounds for vectorized draws
    draws_a = math.ceil(n_a / L_a_used)
    draws_b = math.ceil(n_b / L_b_used)
    high_a = n_a - L_a_used + 1
    high_b = n_b - L_b_used + 1

    # Index arrays to reconstruct block samples without Python list.append loops
    a_idx = np.arange(L_a_used)   # offsets within a block
    b_idx_arr = np.arange(L_b_used)

    boot_diffs_shifted = np.empty(n_boot)
    boot_diffs_unshifted = np.empty(n_boot)

    for b_idx in range(n_boot):
        # --- slot 1: shifted_a ---
        starts = rng.integers(0, high_a, size=draws_a)
        idx_full = (starts[:, None] + a_idx[None, :]).ravel()[:n_a]
        samp_a_shifted = shifted_a[idx_full]

        # --- slot 2: shifted_b ---
        starts = rng.integers(0, high_b, size=draws_b)
        idx_full = (starts[:, None] + b_idx_arr[None, :]).ravel()[:n_b]
        samp_b_shifted = shifted_b[idx_full]

        boot_diffs_shifted[b_idx] = samp_a_shifted.mean() - samp_b_shifted.mean()

        # --- slot 3: raw a ---
        starts = rng.integers(0, high_a, size=draws_a)
        idx_full = (starts[:, None] + a_idx[None, :]).ravel()[:n_a]
        samp_a_raw = a[idx_full]

        # --- slot 4: raw b ---
        starts = rng.integers(0, high_b, size=draws_b)
        idx_full = (starts[:, None] + b_idx_arr[None, :]).ravel()[:n_b]
        samp_b_raw = b[idx_full]

        boot_diffs_unshifted[b_idx] = samp_a_raw.mean() - samp_b_raw.mean()

    # COR-01: one-sided p (positive side: H_a: Q5 > Q1)
    p = float(np.mean(boot_diffs_shifted >= obs_diff))
    return p, boot_diffs_unshifted, L_a_used, capped_a, L_b_used, capped_b


def _mbb_pvalue_onesample(
    arr: np.ndarray,
    block_size: int,
    n_boot: int,
    rng: np.random.Generator,
) -> float:
    """One-sample MBB p-value for H0: mean=0 (positive side), H2 Q5 absolute.

    COR-02: one-sided p (positive side): frac(boot_means >= obs_mean).
    ADV-03: returns nan when n<2.

    PERF-07: Per-iteration vectorization of the while-loop draws; same RNG stream
    as original (rng.integers(size=k) is stream-equivalent to k scalar calls).
    """
    n = len(arr)
    if n == 0:
        return 1.0
    # ADV-03: degenerate — cannot bootstrap with n<2
    if n < 2:
        return float("nan")
    obs_mean = float(np.mean(arr))
    shifted = arr - obs_mean
    L = max(1, min(block_size, max(n // 2, 1)))
    high = n - L + 1
    draws = math.ceil(n / L)
    blk_idx = np.arange(L)  # offsets within a block
    boot_means = np.empty(n_boot)
    for b_idx in range(n_boot):
        starts = rng.integers(0, high, size=draws)
        idx_full = (starts[:, None] + blk_idx[None, :]).ravel()[:n]
        boot_means[b_idx] = shifted[idx_full].mean()
    # COR-02: one-sided p (positive side)
    return float(np.mean(boot_means >= obs_mean))


def _compute_mde_q5q1(q5_arr: np.ndarray, q1_arr: np.ndarray) -> float:
    """MDE of Q5-Q1 difference in percentage points.

    § 3a: MDE = (1.96 + 0.842) * sqrt(s5²/n5 + s1²/n1)
    Q5−Q1 MDE in PERCENTAGE POINTS — excess values already in pct units.

    ADV-03: n=1 has infinite MDE (cannot estimate variance from a single observation).
    Using 0.0 (the prior behaviour) created a false gate-pass; now returns inf.
    """
    n5, n1 = len(q5_arr), len(q1_arr)
    if n5 == 0 or n1 == 0:
        return float("inf")
    # ADV-03: n=1 → std is undefined → MDE is infinite
    if n5 < 2 or n1 < 2:
        return float("inf")
    s5 = float(np.std(q5_arr, ddof=1))
    s1 = float(np.std(q1_arr, ddof=1))
    mde = (1.96 + 0.842) * math.sqrt(s5 ** 2 / n5 + s1 ** 2 / n1)
    return float(mde)


def _nw_pvalue_for_diff(
    q5_arr: np.ndarray,
    q1_arr: np.ndarray,
    block_size: int,
) -> float:
    """NW HAC t-test cross-check on Q5-Q1 difference.

    Brief: 'NW cross-check: adapt event_study._nw_ttest_pvalue via dummy regression
    (excess ~ 1[Q5]) or equivalent'

    Implements via the combined difference array: assign +1 to Q5 obs and 0 to Q1,
    test H0: mean excess of Q5 == mean excess of Q1 via the two-sample NW approach.
    We use the simpler approach: compute the per-event difference array and run NW on it.
    (This is the standard NW t-test on the mean of d_i = x5_i - x1_i when balanced,
    but since Q5 and Q1 may differ in size we use the dummy regression route:
    fit excess ~ intercept + dummy_Q5, test the dummy coefficient.)
    """
    import sys
    from pathlib import Path as _Path
    _backend = _Path(__file__).resolve().parent.parent
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))
    from research.power_audit import _nw_ttest_pvalue

    # Combine into a regression: label Q5=1, Q1=0 then regress excess on dummy.
    # Equivalently: sort union by entry_date, compute the centered contrast.
    # For a simpler NW cross-check: run NW on (q5 - q5.mean) - (q1 - q1.mean)
    # + obs_diff, i.e. use the pooled "excess from mean" to estimate HAC SE.
    # We use the straightforward approach: construct pseudo-residuals for dummy regression.
    n5, n1 = len(q5_arr), len(q1_arr)
    if n5 == 0 or n1 == 0:
        return 1.0
    # Stack: all Q5 first (coded 1), then Q1 (coded 0)
    y = np.concatenate([q5_arr, q1_arr])
    d = np.array([1.0] * n5 + [0.0] * n1)
    # OLS: beta = (sum d*y - n*d_bar*y_bar) / (sum d^2 - n*d_bar^2)
    n = len(y)
    y_bar, d_bar = float(np.mean(y)), float(np.mean(d))
    denom = float(np.dot(d - d_bar, d - d_bar))
    if denom < 1e-15:
        return 1.0
    beta = float(np.dot(d - d_bar, y - y_bar)) / denom
    # Residuals
    alpha = y_bar - beta * d_bar
    resid = y - (alpha + beta * d)
    # NW SE of beta: Var(beta) = Var(1/n * sum((d-d_bar)*e_i)) / (1/n * sum((d-d_bar)^2))^2
    # Use _nw_ttest_pvalue on the "score" array (d-d_bar)*resid, divided by denom/n
    score_arr = (d - d_bar) * resid
    nw_lag = max(0, block_size - 1)
    # NW variance of beta: omega_score / (denom/n)^2 / n
    # We can get p via t-test on beta / se_nw:
    # Compute NW se directly
    n_eff = n
    gamma0 = float(np.dot(score_arr, score_arr)) / n_eff
    omega = gamma0
    for j in range(1, nw_lag + 1):
        w_j = 1.0 - j / (nw_lag + 1)
        gamma_j = float(np.dot(score_arr[j:], score_arr[:-j])) / n_eff
        omega += 2.0 * w_j * gamma_j
    var_beta = max(omega, 1e-30) / ((denom / n_eff) ** 2 * n_eff)
    se_beta = math.sqrt(var_beta)
    t_stat = beta / se_beta
    from scipy import stats as scipy_stats
    p_nw = float(2.0 * scipy_stats.t.sf(abs(t_stat), df=n - 2))
    return p_nw


def _per_quintile_stats(
    valid_rows: list[dict],
    quintiles: list[Optional[int]],
    horizon: int,
    use_peer: bool = False,
) -> dict:
    """Compute per-quintile mean excess and counts at given horizon.

    Returns {q_label: {mean, n, values_sorted_by_entry_date}}.
    """
    getter = _get_peer_excess if use_peer else _get_excess
    groups: dict[int, list[tuple[str, float]]] = {q: [] for q in range(1, N_QUINTILES + 1)}
    for i, (row, q) in enumerate(zip(valid_rows, quintiles)):
        if q is None:
            continue
        excess = getter(row, horizon)
        if excess is None:
            continue
        entry_date_str = row.get("entry_date", "")
        groups[q].append((entry_date_str, excess))

    result: dict = {}
    for q_label, items in groups.items():
        items_sorted = sorted(items, key=lambda x: x[0])
        vals = np.array([v for _, v in items_sorted], dtype=float)
        result[q_label] = {
            "mean": float(np.mean(vals)) if len(vals) > 0 else None,
            "n": len(vals),
            "values": vals,  # sorted by entry_date
        }
    return result


def _per_year_quintile_rho(
    valid_rows: list[dict],
    quintiles: list[Optional[int]],
    horizon: int,
) -> dict:
    """Per-year Spearman ρ_s (quintile index vs per-quintile mean excess) and fraction > 0."""
    # Group by year, compute per-year quintile means
    year_data: dict[int, dict[int, list[float]]] = {}
    for row, q in zip(valid_rows, quintiles):
        if q is None:
            continue
        year = _get_entry_year(row)
        if year is None:
            continue
        excess = _get_excess(row, horizon)
        if excess is None:
            continue
        year_data.setdefault(year, {}).setdefault(q, []).append(excess)

    per_year_rho: dict[int, Optional[float]] = {}
    for year in sorted(year_data.keys()):
        q_means = []
        q_labels = []
        for q in range(1, N_QUINTILES + 1):
            vals = year_data[year].get(q)
            if vals:
                q_labels.append(q)
                q_means.append(float(np.mean(vals)))
        if len(q_labels) < 2:
            per_year_rho[year] = None
            continue
        from scipy.stats import spearmanr
        rho, _ = spearmanr(q_labels, q_means)
        per_year_rho[year] = float(rho)

    # ADV-06: exclude nan rhos (degenerate years) from both numerator and denominator.
    # Also track degenerate years separately.
    n_degenerate_years = sum(
        1 for r in per_year_rho.values()
        if r is not None and not math.isfinite(r)
    )
    valid_rhos = [r for r in per_year_rho.values() if r is not None and math.isfinite(r)]
    frac_positive = (
        sum(1 for r in valid_rhos if r > 0) / len(valid_rhos) if valid_rhos else None
    )
    return {
        "per_year": per_year_rho,
        "frac_years_positive": frac_positive,
        "n_degenerate_years": n_degenerate_years,
    }


def _perturbation_band(
    valid_rows: list[dict],
    quintiles_primary: list[Optional[int]],  # primary quintile assignments (from primary score)
    horizon: int,
) -> dict:
    """Compute sign(Q5-Q1) and sign(ρ_s) for each perturbation variant.

    Brief: 'Perturbation variants re-derive quintiles from payload["score_perturb"][key];
    an event missing a perturbation key or with None is excluded from that variant only.'

    band_sign_stable = all variants match primary Q5-Q1 sign AND ρ_s sign.

    PERF-06: year_groups built once and shared across all 9 variants; primary stats
    computed once and reused for the primary-sign reference.  Output is identical.
    """
    # Compute primary signs (primary stats already computed upstream but recomputed
    # here for self-containment, consistent with original behaviour)
    primary_stats = _per_quintile_stats(valid_rows, quintiles_primary, horizon)
    q5_mean_primary = primary_stats[5]["mean"]
    q1_mean_primary = primary_stats[1]["mean"]
    primary_gap_sign = (
        1 if (q5_mean_primary is not None and q1_mean_primary is not None and
              q5_mean_primary - q1_mean_primary > 0)
        else (-1 if (q5_mean_primary is not None and q1_mean_primary is not None and
                     q5_mean_primary - q1_mean_primary < 0) else 0)
    )

    # Compute primary ρ_s sign
    q_indices = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    q_means_primary = np.array([
        primary_stats[q]["mean"] if primary_stats[q]["mean"] is not None else float("nan")
        for q in range(1, 6)
    ])
    if not np.any(np.isnan(q_means_primary)) and len(q_means_primary) == 5:
        from scipy.stats import spearmanr
        rho_p, _ = spearmanr(q_indices, q_means_primary)
        primary_rho_sign = 1 if rho_p > 0 else (-1 if rho_p < 0 else 0)
    else:
        primary_rho_sign = 0

    # PERF-06: build year_groups once; reused for every variant key
    year_groups = _build_year_groups(valid_rows)

    band_table: dict[str, dict] = {}
    all_match = True

    for key in PERTURB_KEYS:
        def score_fn_for_key(row: dict, _key: str = key) -> Optional[float]:
            return _get_perturb_score(row, _key)

        # Re-derive quintiles from this variant using the shared year_groups
        var_quintiles = _assign_quintiles_all_years_from_groups(
            valid_rows, year_groups, score_fn_for_key
        )

        # Filter: only rows where this perturbation key has a score
        filtered_rows = []
        filtered_quintiles = []
        excluded_count = 0
        for row, q in zip(valid_rows, var_quintiles):
            ps = _get_perturb_score(row, key)
            if ps is None:
                excluded_count += 1
                continue
            filtered_rows.append(row)
            filtered_quintiles.append(q)

        var_stats = _per_quintile_stats(filtered_rows, filtered_quintiles, horizon)
        q5_m = var_stats[5]["mean"]
        q1_m = var_stats[1]["mean"]
        gap = (q5_m - q1_m) if (q5_m is not None and q1_m is not None) else None
        gap_sign = 1 if (gap is not None and gap > 0) else (-1 if (gap is not None and gap < 0) else 0)

        q_means_var = np.array([
            var_stats[q]["mean"] if var_stats[q]["mean"] is not None else float("nan")
            for q in range(1, 6)
        ])
        if not np.any(np.isnan(q_means_var)) and len(q_means_var) == 5:
            from scipy.stats import spearmanr
            rho_v, _ = spearmanr(q_indices, q_means_var)
            rho_sign = 1 if rho_v > 0 else (-1 if rho_v < 0 else 0)
        else:
            rho_sign = 0

        matches_primary = (gap_sign == primary_gap_sign) and (rho_sign == primary_rho_sign)
        if key != PRIMARY_PERTURB_KEY and not matches_primary:
            all_match = False

        band_table[key] = {
            "gap": round(gap, 4) if gap is not None else None,
            "gap_sign": gap_sign,
            "rho_sign": rho_sign,
            "excluded_events": excluded_count,
            "is_primary": key == PRIMARY_PERTURB_KEY,
        }

    return {
        "band_sign_stable": all_match,
        "band_table": band_table,
        "primary_gap_sign": primary_gap_sign,
        "primary_rho_sign": primary_rho_sign,
    }


def _regime_lens(
    valid_rows: list[dict],
    quintiles: list[Optional[int]],
    horizon: int,
) -> dict:
    """Per-regime Q5-Q1 breakdown.

    F367 charter §4 label fix:
    - STRESS is ALWAYS non-evidential regardless of n — it is a rare crisis
      state ("never load-bearing") and carries the suffix annotation in the
      report.  The n>=15 evidential gate applies only to RISK_ON and NEUTRAL.
    - RISK_OFF (the original RARE_NON_EVIDENTIAL_STATE) remains always
      non-evidential (3 days in 2015-2020 — vanishingly rare).
    - Evidential pair: {RISK_ON, NEUTRAL} only.

    is_stress_non_evidential key covers both STRESS and RISK_OFF (any state
    that is never evidential regardless of n).  Key name kept for
    artifact-schema stability.
    regime_unresolved counted separately.
    """
    evidential_states = {"RISK_ON", "NEUTRAL"}
    # States that are ALWAYS non-evidential regardless of n (rare/crisis states)
    always_non_evidential_states = {RARE_NON_EVIDENTIAL_STATE, "STRESS"}
    state_groups: dict[str, list[tuple[int, float]]] = {
        "RISK_ON": [], "NEUTRAL": [], "RISK_OFF": [], "STRESS": [],
    }
    regime_unresolved = 0

    for row, q in zip(valid_rows, quintiles):
        if q is None:
            continue
        excess = _get_excess(row, horizon)
        if excess is None:
            continue
        regime = row.get("regime_state")
        if regime in state_groups:
            state_groups[regime].append((q, excess))
        else:
            regime_unresolved += 1

    breakdown: dict = {}
    for state, items in state_groups.items():
        # Q5 items and Q1 items
        q5_vals = np.array([e for q, e in items if q == 5], dtype=float)
        q1_vals = np.array([e for q, e in items if q == 1], dtype=float)
        n_total = len(items)

        gap: Optional[float] = None
        gap_sign: Optional[int] = None
        if len(q5_vals) > 0 and len(q1_vals) > 0:
            gap = float(np.mean(q5_vals)) - float(np.mean(q1_vals))
            gap_sign = 1 if gap > 0 else (-1 if gap < 0 else 0)

        is_always_non_evidential = (state in always_non_evidential_states)
        is_evidential = (
            not is_always_non_evidential
            and state in evidential_states
            and n_total >= REGIME_EVIDENTIAL_MIN
        )

        breakdown[state] = {
            "n_valid_q5q1": int(len(q5_vals) + len(q1_vals)),
            "n_q5": int(len(q5_vals)),
            "n_q1": int(len(q1_vals)),
            "n_total_quintile_valid": n_total,
            "gap_q5q1": round(gap, 4) if gap is not None else None,
            "gap_sign": gap_sign,
            "is_evidential": is_evidential,
            # is_stress_non_evidential: True for any always-non-evidential state
            # (RISK_OFF and STRESS).  Key name kept for artifact-schema stability.
            "is_stress_non_evidential": is_always_non_evidential,
        }

    # REGIME-CARRIED qualifier: positive in exactly ONE of evidential states, <=0 in others
    # (each evidential and >=15 events); only RISK_ON/NEUTRAL can qualify.
    evidential_cells = [
        (state, breakdown[state])
        for state in evidential_states
        if breakdown[state]["is_evidential"]
    ]
    regime_carried = False
    regime_carried_state: Optional[str] = None
    if len(evidential_cells) >= 2:
        positive_cells = [s for s, d in evidential_cells if (d["gap_sign"] or 0) > 0]
        nonpositive_cells = [s for s, d in evidential_cells if (d["gap_sign"] or 0) <= 0]
        if len(positive_cells) == 1 and len(nonpositive_cells) == len(evidential_cells) - 1:
            regime_carried = True
            regime_carried_state = positive_cells[0]

    return {
        "per_state": breakdown,
        "regime_unresolved": regime_unresolved,
        "regime_carried": regime_carried,
        "regime_carried_state": regime_carried_state,
    }


def _peer_lens_summary(
    valid_rows: list[dict],
    quintiles: list[Optional[int]],
    horizon: int,
) -> dict:
    """Peer lens: same quintile assignment from primary score, reads excess_peer.

    Brief: 'The peer lens uses the SAME quintile assignment as the primary
    (from the frozen score), reading excess_peer instead of excess_univ.'
    """
    peer_stats = _per_quintile_stats(valid_rows, quintiles, horizon, use_peer=True)
    q5_m = peer_stats[5]["mean"]
    q1_m = peer_stats[1]["mean"]
    gap: Optional[float] = (q5_m - q1_m) if (q5_m is not None and q1_m is not None) else None
    gap_sign: Optional[int] = None
    if gap is not None:
        gap_sign = 1 if gap > 0 else (-1 if gap < 0 else 0)

    # Spearman sign on peer
    q_indices = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    q_means = np.array([
        peer_stats[q]["mean"] if peer_stats[q]["mean"] is not None else float("nan")
        for q in range(1, 6)
    ])
    rho_s_sign: Optional[int] = None
    if not np.any(np.isnan(q_means)):
        from scipy.stats import spearmanr
        rho_p, _ = spearmanr(q_indices, q_means)
        rho_s_sign = 1 if rho_p > 0 else (-1 if rho_p < 0 else 0)

    # Peer fallback rate
    fallback_count = 0
    total_with_peer = 0
    univ_excess_vals = []
    peer_excess_vals = []
    for row, q in zip(valid_rows, quintiles):
        if q is None:
            continue
        univ_e = _get_excess(row, horizon)
        peer_e = _get_peer_excess(row, horizon)
        if univ_e is None or peer_e is None:
            continue
        total_with_peer += 1
        univ_excess_vals.append(univ_e)
        peer_excess_vals.append(peer_e)
        fallback_level = row.get("peer_sic_fallback_level", "")
        if fallback_level != "3_digit":
            fallback_count += 1

    fallback_rate = fallback_count / total_with_peer if total_with_peer > 0 else None
    underpowered = (fallback_rate is not None and fallback_rate > 0.40)

    # Pearson corr(excess_univ, excess_peer)
    pearson_corr: Optional[float] = None
    if len(univ_excess_vals) >= 2:
        corr_mat = np.corrcoef(univ_excess_vals, peer_excess_vals)
        pearson_corr = float(corr_mat[0, 1])

    return {
        "gap_q5q1": round(gap, 4) if gap is not None else None,
        "gap_sign": gap_sign,
        "rho_s_sign": rho_s_sign,
        "fallback_rate": round(fallback_rate, 4) if fallback_rate is not None else None,
        "underpowered": underpowered,
        "pearson_corr_univ_peer": round(pearson_corr, 4) if pearson_corr is not None else None,
        "n_with_peer": total_with_peer,
    }


def _config_hash(seed: int, primary_horizon: int = PRIMARY_HORIZON, all_horizons: tuple = ALL_HORIZONS) -> str:
    """Compute a short config hash covering frozen score constants + analysis params.

    Charter §5: 'config-hash incl. score constants, q, n_boot, per-test block sizes/p/n'
    F410: primary_horizon and all_horizons are params (defaults = module constants) so R-1
    charter runs produce bit-identical hashes while premise explores can override.
    """
    config_str = json.dumps({
        "W": 21, "beta": 0.5, "log_shape": "log1p", "mc_construction": "shares_outstanding_x_close",
        "q": FDR_Q, "n_boot": N_BOOT, "seed": seed,
        "horizons": list(all_horizons), "primary_horizon": primary_horizon,
        "mde_abort_pp": MDE_ABORT_PP, "n_quintiles": N_QUINTILES,
        "perturb_keys": PERTURB_KEYS,
    }, sort_keys=True)
    return hashlib.sha256(config_str.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_r1_analysis(
    study_dir: Path,
    *,
    seed: int = SEED,
    ledger_path: Optional[Path] = None,
    primary_horizon: Optional[int] = None,   # None → use PRIMARY_HORIZON (63) default
    horizons: Optional[tuple[int, ...]] = None,  # None → use ALL_HORIZONS default
) -> dict:
    """R-1 explore analysis.

    Reads events.ndjson + meta.json from study_dir.
    Runs H1/H1b/H2, perturbation band, peer + regime lenses.
    Writes r1_explore_verdict.json into study_dir.
    Appends one charter-family entry to the FDR ledger ONLY when ledger_path
    is given explicitly; ledger_path=None skips the append with a warning
    (guard against test suites polluting the real ledger — see _append_r1_ledger).

    F410: primary_horizon and horizons default to None → module constants (63, (21,63,126)).
    R-1 charter calls omitting these params produce bit-identical output including config_hash.
    63td is always included in the H3 secondary horizons set for comparability.

    Returns the result dict (same as what was written to r1_explore_verdict.json).
    """
    study_dir = Path(study_dir)
    rng = np.random.default_rng(seed)  # § SEED: all RNG seeded from this

    # F410: Resolve horizon params (None → module defaults, preserving R-1 bit-identical behavior)
    _primary = primary_horizon if primary_horizon is not None else PRIMARY_HORIZON
    _horizons = horizons if horizons is not None else ALL_HORIZONS
    # 63td comparability: always include 63 in the horizons set for H3, even if spec omits it.
    # Constraint (John 2026-06-10): 63td secondary is a reporting lens ONLY — not pass/fail.
    _horizons_with_63 = tuple(sorted(set(_horizons) | {63}))

    # ------------------------------------------------------------------
    # 1. Load artifacts
    # ------------------------------------------------------------------
    all_rows, meta = _load_events(study_dir)
    study_name = meta.get("study_name", study_dir.name)

    # ------------------------------------------------------------------
    # 2. Filter valid events
    # ------------------------------------------------------------------
    valid_rows = [r for r in all_rows if _is_valid_event(r, primary_horizon=_primary)]
    n_valid = len(valid_rows)
    n_score_undefined = sum(
        1 for r in all_rows
        if r.get("split") == "explore" and (r.get("payload") or {}).get("score") is None
    )

    # ------------------------------------------------------------------
    # 3. Quintile assignment (within-year, primary score)
    # ------------------------------------------------------------------
    quintiles = _assign_quintiles_all_years(valid_rows)

    # Per-year per-quintile counts (for thinness visibility)
    year_quintile_counts: dict[str, dict[str, int]] = {}
    for row, q in zip(valid_rows, quintiles):
        year = _get_entry_year(row)
        if year is None or q is None:
            continue
        yr_key = str(year)
        year_quintile_counts.setdefault(yr_key, {})
        q_key = str(q)
        year_quintile_counts[yr_key][q_key] = year_quintile_counts[yr_key].get(q_key, 0) + 1

    # ADV-08: detect years where all scores are identical (zero score variance)
    year_scores: dict[int, list[float]] = {}
    for row in valid_rows:
        year = _get_entry_year(row)
        if year is None:
            continue
        s = _get_score(row)
        if s is None:
            continue
        year_scores.setdefault(year, []).append(s)
    degenerate_score_years: list[int] = [
        yr for yr, scores in year_scores.items()
        if len(scores) > 0 and float(np.std(scores)) == 0.0
    ]

    # ------------------------------------------------------------------
    # 4. Per-quintile stats at primary horizon
    # ------------------------------------------------------------------
    pq_stats = _per_quintile_stats(valid_rows, quintiles, _primary)
    q5_vals = pq_stats[5]["values"]
    q1_vals = pq_stats[1]["values"]
    q5_mean = pq_stats[5]["mean"]
    q1_mean = pq_stats[1]["mean"]
    n_q5 = pq_stats[5]["n"]
    n_q1 = pq_stats[1]["n"]
    obs_gap = (q5_mean - q1_mean) if (q5_mean is not None and q1_mean is not None) else None

    # Per-quintile means for Spearman
    q_indices = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    q_means_arr = np.array([
        pq_stats[q]["mean"] if pq_stats[q]["mean"] is not None else float("nan")
        for q in range(1, 6)
    ])

    # ------------------------------------------------------------------
    # 5. MDE gate (§ 3a)
    # ------------------------------------------------------------------
    mde_q5q1 = _compute_mde_q5q1(q5_vals, q1_vals)
    mde_gate_passed = (mde_q5q1 <= MDE_ABORT_PP)

    # ------------------------------------------------------------------
    # 6. Block sizes for bootstraps
    # ------------------------------------------------------------------
    from research.event_study import _block_size_for_horizon  # reuse harness

    # For Q5 and Q1, sort by entry_date to get the dates for block-size calc
    q5_dates = sorted([
        date.fromisoformat(row.get("entry_date", ""))
        for row, q in zip(valid_rows, quintiles)
        if q == 5 and row.get("entry_date")
    ])
    q1_dates = sorted([
        date.fromisoformat(row.get("entry_date", ""))
        for row, q in zip(valid_rows, quintiles)
        if q == 1 and row.get("entry_date")
    ])
    block_size_q5 = _block_size_for_horizon(_primary, q5_dates)
    block_size_q1 = _block_size_for_horizon(_primary, q1_dates)

    # For H2 (Q5 absolute mean)
    block_size_h2 = block_size_q5

    # For NW cross-check
    nw_lag_q5q1 = max(block_size_q5, block_size_q1) - 1

    # ------------------------------------------------------------------
    # 7. H1: Q5-Q1 two-sample MBB bootstrap (§ 4)
    # ------------------------------------------------------------------
    p_h1_boot = 1.0
    ci_low_h1 = None
    ci_high_h1 = None
    block_size_q5_used: Optional[int] = None
    block_size_q1_used: Optional[int] = None
    block_size_q5_capped: bool = False
    block_size_q1_capped: bool = False
    h1_boot_degenerate: bool = False  # ADV-03: n<2 for either quintile
    if len(q5_vals) > 0 and len(q1_vals) > 0:
        (p_h1_boot_raw, boot_diffs_unshifted,
         block_size_q5_used, block_size_q5_capped,
         block_size_q1_used, block_size_q1_capped) = _two_sample_mbb_bootstrap(
            q5_vals, q1_vals,
            block_size_q5, block_size_q1,
            N_BOOT, rng,
        )
        if math.isnan(p_h1_boot_raw):
            # ADV-03: degenerate (n<2)
            h1_boot_degenerate = True
            p_h1_boot = 1.0
        else:
            p_h1_boot = p_h1_boot_raw
            if len(boot_diffs_unshifted) > 0:
                ci_low_h1 = float(np.percentile(boot_diffs_unshifted, 2.5))
                ci_high_h1 = float(np.percentile(boot_diffs_unshifted, 97.5))

    # NW cross-check.  _nw_pvalue_for_diff is TWO-sided; the H1 bootstrap p is
    # ONE-sided (positive side, COR-01).  The charter's "agree within 0.10"
    # check must compare like with like, so convert the NW p to its one-sided
    # (positive-side) equivalent using the observed gap's sign before the gap
    # check.  Both raw values are reported.
    p_h1_nw = _nw_pvalue_for_diff(q5_vals, q1_vals, nw_lag_q5q1) if len(q5_vals) > 0 and len(q1_vals) > 0 else 1.0
    if obs_gap is not None and obs_gap > 0:
        p_h1_nw_onesided = p_h1_nw / 2.0
    else:
        p_h1_nw_onesided = 1.0 - p_h1_nw / 2.0
    nw_boot_gap_flag = (abs(p_h1_boot - p_h1_nw_onesided) > 0.10)

    # ------------------------------------------------------------------
    # 8. H1b: Spearman ρ_s with exact permutation p (§ 4)
    # ------------------------------------------------------------------
    rho_s = None
    p_h1b = 1.0
    if not np.any(np.isnan(q_means_arr)) and len(q_means_arr) == N_QUINTILES:
        rho_s, p_h1b = _spearman_exact_onesided(q_indices, q_means_arr)
        # ADV-01: _spearman_exact_onesided already returns (None, 1.0) for degenerate case
    else:
        rho_s = None

    # ADV-07: sanitize nan rho_s to None for JSON safety
    if rho_s is not None and math.isnan(rho_s):
        rho_s = None

    # Per-year Spearman
    per_year_rho_info = _per_year_quintile_rho(valid_rows, quintiles, _primary)

    # ------------------------------------------------------------------
    # 9. H2: Q5 absolute mean (§ 4)
    # ------------------------------------------------------------------
    p_h2_raw = 1.0
    p_h2 = 1.0
    h2_boot_degenerate: bool = False  # ADV-03
    q5_abs_mean = q5_mean
    if len(q5_vals) > 0:
        p_h2_raw = _mbb_pvalue_onesample(q5_vals, block_size_h2, N_BOOT, rng)
        if math.isnan(p_h2_raw):
            h2_boot_degenerate = True
            p_h2 = 1.0
        else:
            p_h2 = p_h2_raw

    # ------------------------------------------------------------------
    # 10. BH FDR family {H1, H1b, H2} at q=0.10 (§ 5)
    # ------------------------------------------------------------------
    from research.event_study import FDRLedger as _FDRLedger

    # F410: FDR key names are dynamic, based on _primary horizon
    h1_key = f"H1_Q5Q1_{_primary}d"
    h1b_key = f"H1b_spearman_{_primary}d"
    h2_key = f"H2_Q5abs_{_primary}d"

    fdr = _FDRLedger(q=FDR_Q)
    fdr.add(h1_key, p_value=p_h1_boot,
            description=f"Q5-Q1 {_primary}td mean universe excess (two-sample MBB bootstrap, one-sided)")
    fdr.add(h1b_key, p_value=p_h1b,
            description=f"Spearman ρ_s quintile index vs mean {_primary}td excess (exact permutation, one-sided)")
    fdr.add(h2_key, p_value=p_h2,
            description=f"Q5 absolute {_primary}td mean excess (MBB bootstrap, H0: mean=0, one-sided)")
    fdr_report = fdr.finalize()

    # COR-01: H1 rejection only valid when obs_gap > 0 (positive-side test)
    h1_rejected = fdr_report[h1_key]["rejected"] and (obs_gap is not None and obs_gap > 0)
    # H1b BH rejection is already only from positive-side exact-permutation p
    h1b_rejected = fdr_report[h1b_key]["rejected"]
    # COR-02: H2 rejection only valid when q5_abs_mean > 0 (positive-side test)
    h2_rejected = fdr_report[h2_key]["rejected"] and (q5_abs_mean is not None and q5_abs_mean > 0)

    # Patch bh_rejected flags back into fdr_report for ledger/verdict consistency
    fdr_report[h1_key]["rejected"] = h1_rejected
    fdr_report[h2_key]["rejected"] = h2_rejected

    # ------------------------------------------------------------------
    # 11. Perturbation band (§ 3b)
    # ------------------------------------------------------------------
    band_result = _perturbation_band(valid_rows, quintiles, _primary)
    band_sign_stable = band_result["band_sign_stable"]

    # ------------------------------------------------------------------
    # 12. Lenses
    # ------------------------------------------------------------------
    # Era lens (per-year Q5-Q1 + ρ_s signs)
    era_data: dict[str, dict] = {}
    year_groups_data: dict[int, list] = {}
    for row, q in zip(valid_rows, quintiles):
        year = _get_entry_year(row)
        if year is None:
            continue
        year_groups_data.setdefault(year, []).append((row, q))

    for year in sorted(year_groups_data.keys()):
        items = year_groups_data[year]
        q5_y = np.array([
            _get_excess(r, _primary)
            for r, q in items if q == 5 and _get_excess(r, _primary) is not None
        ], dtype=float)
        q1_y = np.array([
            _get_excess(r, _primary)
            for r, q in items if q == 1 and _get_excess(r, _primary) is not None
        ], dtype=float)
        gap_y = (float(np.mean(q5_y)) - float(np.mean(q1_y))) if (len(q5_y) > 0 and len(q1_y) > 0) else None

        q_means_y = []
        q_has_all = True
        for qv in range(1, N_QUINTILES + 1):
            vy = np.array([
                _get_excess(r, _primary)
                for r, q in items if q == qv and _get_excess(r, _primary) is not None
            ], dtype=float)
            if len(vy) == 0:
                q_has_all = False
                q_means_y.append(float("nan"))
            else:
                q_means_y.append(float(np.mean(vy)))

        rho_y_sign: Optional[int] = None
        if q_has_all and not any(math.isnan(v) for v in q_means_y):
            from scipy.stats import spearmanr
            rho_y_val, _ = spearmanr([1, 2, 3, 4, 5], q_means_y)
            rho_y_sign = 1 if rho_y_val > 0 else (-1 if rho_y_val < 0 else 0)

        era_data[str(year)] = {
            "gap_q5q1": round(gap_y, 4) if gap_y is not None else None,
            "gap_sign": 1 if (gap_y is not None and gap_y > 0) else (-1 if (gap_y is not None and gap_y < 0) else 0),
            "rho_s_sign": rho_y_sign,
            "n_q5": int(len(q5_y)),
            "n_q1": int(len(q1_y)),
        }

    # H3: secondary horizons (descriptive) — iterates _horizons_with_63 so 63td is always present
    h3_data: dict[str, dict] = {}
    for h in _horizons_with_63:
        pq_h = _per_quintile_stats(valid_rows, quintiles, h)
        q5_m_h = pq_h[5]["mean"]
        q1_m_h = pq_h[1]["mean"]
        gap_h = (q5_m_h - q1_m_h) if (q5_m_h is not None and q1_m_h is not None) else None
        q_means_h = np.array([
            pq_h[q]["mean"] if pq_h[q]["mean"] is not None else float("nan")
            for q in range(1, 6)
        ])
        rho_h: Optional[float] = None
        if not np.any(np.isnan(q_means_h)):
            from scipy.stats import spearmanr
            rho_h_val, _ = spearmanr(q_indices, q_means_h)
            rho_h = float(rho_h_val)
        h3_data[str(h)] = {
            "gap_q5q1": round(gap_h, 4) if gap_h is not None else None,
            "rho_s": round(rho_h, 4) if rho_h is not None else None,
            "is_primary": (h == _primary),
            "n_q5": int(pq_h[5]["n"]),
            "n_q1": int(pq_h[1]["n"]),
        }

    # Peer lens
    peer_lens = _peer_lens_summary(valid_rows, quintiles, _primary)

    # Regime lens
    regime_lens = _regime_lens(valid_rows, quintiles, _primary)

    # ------------------------------------------------------------------
    # 13. Explore decision (§ 4 / § 9)
    # ------------------------------------------------------------------
    # Charter-frozen thresholds (encoded as named constants above):
    # ADVANCE iff gap>0 AND ρ_s>0 AND band_sign_stable AND MDE<=1.0pp
    gap_positive = (obs_gap is not None and obs_gap > ADVANCE_GAP_GT)
    rho_positive = (rho_s is not None and rho_s > ADVANCE_RHO_GT)

    # ADV-03: MDE=inf (n<2 in Q5 or Q1) → cannot evaluate power → UNTESTABLE
    mde_not_evaluable = (n_q5 < 2 or n_q1 < 2)

    if mde_not_evaluable:
        explore_decision = "UNTESTABLE — power not evaluable"
    elif not mde_gate_passed:
        explore_decision = "UNTESTABLE-underpowered"
    elif gap_positive and rho_positive and band_sign_stable:
        explore_decision = "ADVANCE"
    else:
        explore_decision = "WEAKENED-IN-EXPLORE"

    # ------------------------------------------------------------------
    # 14. Build result dict
    # ------------------------------------------------------------------
    # F410/C4 — intentional split between verdict identity and execution record:
    #   verdict config_hash covers spec horizons (_horizons, NOT _horizons_with_63)
    #   so the hash identifies the spec intent and is bit-identical to pre-F410 for defaults.
    #   harness meta.json covers the 63-injected set (harness_horizons) — that is the
    #   execution record.  An auditor wanting the full computed set should read meta.json;
    #   the verdict hash is the spec identity, not the full execution fingerprint.
    cfg_hash = _config_hash(seed, primary_horizon=_primary, all_horizons=_horizons)

    per_quintile_summary: dict[str, dict] = {}
    for q_label in range(1, N_QUINTILES + 1):
        per_quintile_summary[str(q_label)] = {
            f"mean_{_primary}d_excess": round(float(pq_stats[q_label]["mean"]), 4) if pq_stats[q_label]["mean"] is not None else None,
            "n": pq_stats[q_label]["n"],
        }

    result = {
        "study_name": study_name,
        "analysis_version": "r1_analysis_v1",
        "config_hash": cfg_hash,
        "seed": seed,
        "primary_horizon": _primary,  # F410: spec-designated primary horizon
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "n_valid_events": n_valid,
        "n_score_undefined": n_score_undefined,
        "year_quintile_counts": year_quintile_counts,
        # ADV-08: degenerate years (zero score variance → quintile ordering is lexicographic noise)
        "degenerate_score_years": sorted(degenerate_score_years),
        "per_quintile": per_quintile_summary,
        "H1": {
            "obs_gap_q5q1_pp": round(obs_gap, 4) if obs_gap is not None else None,
            "ci_low_95": round(ci_low_h1, 4) if ci_low_h1 is not None else None,
            "ci_high_95": round(ci_high_h1, 4) if ci_high_h1 is not None else None,
            "p_boot": round(p_h1_boot, 4),
            "p_nw": round(p_h1_nw, 4),
            "p_nw_onesided": round(p_h1_nw_onesided, 4),
            "nw_boot_gap_flag": nw_boot_gap_flag,
            "block_size_q5": block_size_q5,
            "block_size_q1": block_size_q1,
            # ADV-05: actual block length used after n-based cap
            "block_size_q5_used": block_size_q5_used,
            "block_size_q5_capped": block_size_q5_capped,
            "block_size_q1_used": block_size_q1_used,
            "block_size_q1_capped": block_size_q1_capped,
            "n_q5": n_q5,
            "n_q1": n_q1,
            "bh_rejected": h1_rejected,
            "boot_degenerate": h1_boot_degenerate,  # ADV-03
        },
        "H1b": {
            "rho_s": round(rho_s, 6) if rho_s is not None else None,
            "p_exact_onesided": round(p_h1b, 6),
            "bh_rejected": h1b_rejected,
            "per_year": per_year_rho_info,
        },
        "H2": {
            "q5_abs_mean_pp": round(q5_abs_mean, 4) if q5_abs_mean is not None else None,
            "p_boot": round(p_h2, 4),
            "bh_rejected": h2_rejected,
            "n_q5": n_q5,
            "boot_degenerate": h2_boot_degenerate,  # ADV-03
        },
        "mde_q5q1_pp": round(mde_q5q1, 4) if math.isfinite(mde_q5q1) else None,
        "mde_gate_passed": mde_gate_passed,
        "mde_not_evaluable": mde_not_evaluable,  # ADV-03
        "fdr_report": {
            k: {kk: vv for kk, vv in v.items() if kk != "description"}
            for k, v in fdr_report.items()
        },
        "perturbation_band": band_result,
        "H3_secondary_horizons": h3_data,
        "era_lens": era_data,
        "peer_lens": peer_lens,
        "regime_lens": regime_lens,
        "explore_decision": explore_decision,
        "explore_decision_rationale": {
            "gap_positive": gap_positive,
            "rho_positive": rho_positive,
            "band_sign_stable": band_sign_stable,
            "mde_gate_passed": mde_gate_passed,
            "mde_not_evaluable": mde_not_evaluable,
            "mde_abort_threshold_pp": MDE_ABORT_PP,
        },
    }

    # ADV-07: sanitize nan/inf → null on the full result before JSON write.
    # Traverses the result dict recursively replacing float nan/inf with None.
    def _sanitize_for_json(obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        if isinstance(obj, dict):
            return {k: _sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize_for_json(v) for v in obj]
        return obj

    result = _sanitize_for_json(result)

    # ------------------------------------------------------------------
    # 15. Write r1_explore_verdict.json
    # ------------------------------------------------------------------
    verdict_path = study_dir / "r1_explore_verdict.json"
    _atomic_write(verdict_path, json.dumps(result, indent=2, default=str))
    log.info("R-1 explore verdict written to %s", verdict_path)

    # ------------------------------------------------------------------
    # 16. Append ledger entry (§ 5)
    # ------------------------------------------------------------------
    _append_r1_ledger(
        result=result,
        study_name=study_name,
        cfg_hash=cfg_hash,
        ledger_path=ledger_path,
        primary_horizon=_primary,
        all_horizons=_horizons,
    )

    # ------------------------------------------------------------------
    # 17. Plain-English summary
    # ------------------------------------------------------------------
    _print_plain_english_summary(result)

    return result


# ---------------------------------------------------------------------------
# Ledger append
# ---------------------------------------------------------------------------

def _build_r1_ledger_entry(
    result: dict,
    study_name: str,
    cfg_hash: str,
    primary_horizon: int = PRIMARY_HORIZON,
    all_horizons: tuple = ALL_HORIZONS,
    spec_horizons: tuple | None = None,
) -> dict:
    """Build a charter-family ledger entry dict from an analysis result.

    F410: extracted from _append_r1_ledger so premise_run_worker.py can build
    the entry for its ledger_entry.json sidecar without writing to the real ledger.
    FDR per_test keys are dynamic, based on primary_horizon (e.g. "H1_Q5Q1_30d").

    DI-5/DI-7: all_horizons should be the actual computed set (harness_horizons,
    which always includes 63).  spec_horizons records the declared spec intent.
    When spec_horizons is None (R-1 default path where spec==harness), it is
    omitted from the entry so the field is only present when the two sets differ.
    """
    h1_key = f"H1_Q5Q1_{primary_horizon}d"
    h1b_key = f"H1b_spearman_{primary_horizon}d"
    h2_key = f"H2_Q5abs_{primary_horizon}d"

    entry_study_name = study_name + "_r1_family"
    entry: dict = {
        "study_name": entry_study_name,
        "created_at": result.get("created_at"),
        "study_config_hash": cfg_hash,
        "fdr_q": FDR_Q,
        "n_boot": N_BOOT,
        "horizons": list(all_horizons),
        "primary_horizon": primary_horizon,
    }
    # DI-5/DI-7: record spec_horizons only when the harness injected extra horizons
    # (e.g. 63td forced in for comparability).  Allows post-hoc audit to answer
    # "was 63td computed?" without reading study artifacts directly.
    if spec_horizons is not None and tuple(sorted(spec_horizons)) != tuple(sorted(all_horizons)):
        entry["spec_horizons"] = list(spec_horizons)
    entry.update({
        "per_test": {
            h1_key: {
                "block_size_q5": result["H1"]["block_size_q5"],
                "block_size_q1": result["H1"]["block_size_q1"],
                "p_boot": result["H1"]["p_boot"],
                "p_nw": result["H1"]["p_nw"],
                "n_q5": result["H1"]["n_q5"],
                "n_q1": result["H1"]["n_q1"],
            },
            h1b_key: {
                "p_exact_onesided": result["H1b"]["p_exact_onesided"],
                "rho_s": result["H1b"]["rho_s"],
                "n_quintile_points": N_QUINTILES,
            },
            h2_key: {
                "p_boot": result["H2"]["p_boot"],
                "n_q5": result["H2"]["n_q5"],
            },
        },
        "per_quintile_counts": {
            q_label: result["per_quintile"][q_label]["n"]
            for q_label in result["per_quintile"]
        },
        "perturbation_sign_table": {
            k: {
                "gap_sign": v["gap_sign"],
                "rho_sign": v["rho_sign"],
            }
            for k, v in result["perturbation_band"]["band_table"].items()
        },
        "bh_rejection_set": {
            k: v["rejected"]
            for k, v in result["fdr_report"].items()
        },
        "explore_decision": result["explore_decision"],
        "mde_q5q1_pp": result["mde_q5q1_pp"],
    })
    return entry


def _append_r1_ledger(
    result: dict,
    study_name: str,
    cfg_hash: str,
    ledger_path: Optional[Path],
    primary_horizon: int = PRIMARY_HORIZON,
    all_horizons: tuple = ALL_HORIZONS,
) -> None:
    """Append one charter-family entry to the FDR ledger.

    Brief: 'entry study_name = meta study_name + "_r1_family"'
    Read-modify-write with _atomic_write. NEVER truncate existing entries.
    F410: primary_horizon and all_horizons passed through to _build_r1_ledger_entry.
    """
    import sys
    from pathlib import Path as _Path
    _backend = _Path(__file__).resolve().parent.parent
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))
    from research.event_study import _atomic_write as _aw

    # LEDGER GUARD (2026-06-06): ledger_path=None means SKIP the append, never
    # silently default to the real ledger.  The first run of the test suite
    # defaulted here and wrote 108 synthetic fixture entries into the real
    # backend/data/turnaround/fdr_ledger.json (deleted same day, pre-first-real-
    # entry).  Real runs (run_r1_explore.py) pass the real path EXPLICITLY —
    # writing the permanent alpha-accounting ledger must be a deliberate act.
    if ledger_path is None:
        log.warning(
            "ledger_path=None — FDR ledger append SKIPPED for %s "
            "(pass an explicit path to record this look)", study_name,
        )
        return
    target_path = Path(ledger_path)

    entry = _build_r1_ledger_entry(
        result=result,
        study_name=study_name,
        cfg_hash=cfg_hash,
        primary_horizon=primary_horizon,
        all_horizons=all_horizons,
    )

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_rows: list[dict] = []
        if target_path.exists():
            try:
                ledger_rows = json.loads(target_path.read_text(encoding="utf-8"))
                if not isinstance(ledger_rows, list):
                    ledger_rows = []
            except Exception:
                ledger_rows = []
        ledger_rows.append(entry)
        _aw(target_path, json.dumps(ledger_rows, indent=2, default=str))
    except Exception as exc:
        log.warning("R-1 FDR ledger append failed: %s", exc)


# ---------------------------------------------------------------------------
# Atomic write (local fallback, same pattern as event_study._atomic_write)
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically."""
    import sys
    from pathlib import Path as _Path
    _backend = _Path(__file__).resolve().parent.parent
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))
    try:
        from research.event_study import _atomic_write as _aw
        _aw(path, content)
    except Exception:
        # Fallback
        import os
        import tempfile
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Plain-English summary
# ---------------------------------------------------------------------------

def _print_plain_english_summary(result: dict) -> None:
    """Print a concise plain-English summary — every term defined inline."""
    ph = result.get("primary_horizon", PRIMARY_HORIZON)  # F410: use actual primary horizon
    print("\n" + "=" * 72)
    print("R-1 INSIDER CLUSTER EXPLORE — PLAIN-ENGLISH SUMMARY")
    print("=" * 72)
    print(f"Study: {result['study_name']}")
    print(f"Valid events (explore split, score present, {ph}td excess not null): {result['n_valid_events']}")
    print(f"Score undefined (missing market cap or price): {result['n_score_undefined']}")
    print()
    print("DOSE-RESPONSE (did more insider buying → more forward excess?)")
    gap = result["H1"]["obs_gap_q5q1_pp"]
    ci_low = result["H1"]["ci_low_95"]
    ci_high = result["H1"]["ci_high_95"]
    print(f"  Q5−Q1 gap (top-dose fifth minus bottom-dose fifth, {ph} trading-day excess): "
          f"{gap:.2f}pp" if gap is not None else "  Q5−Q1 gap: N/A")
    if ci_low is not None and ci_high is not None:
        print(f"  95% CI on difference: [{ci_low:.2f}, {ci_high:.2f}]pp")
    p_boot_h1 = result['H1']['p_boot']
    p_nw_h1 = result['H1']['p_nw']
    print(f"  Bootstrap p-value (H0: gap=0): {p_boot_h1:.4f}" if p_boot_h1 is not None else "  Bootstrap p-value: N/A")
    nw_str = f"{p_nw_h1:.4f}" if p_nw_h1 is not None else "N/A"
    print(f"  Newey-West p-value (cross-check): {nw_str}"
          + ("  [NW/boot gap >0.10 — flagged]" if result["H1"]["nw_boot_gap_flag"] else ""))
    rho_s = result["H1b"]["rho_s"]
    p_h1b = result["H1b"]["p_exact_onesided"]
    print(f"\nMONOTONICITY (does the whole dose ladder trend right direction?)")
    print(f"  Spearman rank-correlation ρ_s (1=perfect ladder, -1=reversed): "
          f"{rho_s:.4f}" if rho_s is not None else "  ρ_s: N/A")
    print(f"  Exact permutation p (one-sided, H_a: ρ_s > 0): {p_h1b:.4f}"
          f"  [{1/120:.4f} = p if perfectly monotone, 1/120 of 120 orderings]")
    per_year_rho = result["H1b"]["per_year"]["per_year"]
    frac_pos = result["H1b"]["per_year"].get("frac_years_positive")
    if per_year_rho:
        years_str = ", ".join(
            f"{yr}:{'+' if v > 0 else '-'}" if v is not None else f"{yr}:?"
            for yr, v in sorted(per_year_rho.items())
        )
        print(f"  Per-year ρ_s sign: {years_str}")
        if frac_pos is not None:
            print(f"  Fraction of years with positive monotone trend: {frac_pos:.0%}")
    print(f"\nQ5 ABSOLUTE EXCESS (is top-dose bucket itself positive vs the market?)")
    q5_abs = result["H2"]["q5_abs_mean_pp"]
    print(f"  Q5 mean {ph}td excess: {q5_abs:.2f}pp" if q5_abs is not None else "  N/A")
    print(f"  Bootstrap p-value: {result['H2']['p_boot']:.4f}")
    print(f"\nMDE (smallest gap this test could reliably detect at 80% power):")
    mde = result["mde_q5q1_pp"]
    print(f"  {ph}td Q5−Q1 MDE: {mde:.2f}pp (abort threshold: {MDE_ABORT_PP}pp)" if mde is not None else "  MDE: N/A")
    print(f"  MDE gate passed (<=1.0pp): {result['mde_gate_passed']}")
    print(f"\nPERTURBATION BAND (are results sign-stable to small constant tweaks?):")
    print(f"  All 9 window/floor variants have same sign as primary: {result['perturbation_band']['band_sign_stable']}")
    peer = result["peer_lens"]
    print(f"\nPEER LENS (did top-dose names beat their own industry peers, not just the market?)")
    print(f"  Q5−Q1 peer excess: {peer['gap_q5q1']:.2f}pp" if peer['gap_q5q1'] is not None else "  N/A")
    print(f"  Peer-fallback rate (events without a clean industry peer set): {peer['fallback_rate']:.1%}" if peer['fallback_rate'] is not None else "  N/A")
    print(f"  Peer lens underpowered (>40% fallback): {peer['underpowered']}")
    regime = result["regime_lens"]
    print(f"\nREGIME LENS (does the effect vary by market weather?)")
    _ALWAYS_NON_EVIDENTIAL_NOTES = {
        "RISK_OFF": " — rare crisis state: never load-bearing (3 days in 2015-2020)",
        "STRESS":   " — rare crisis state: never load-bearing",
    }
    for state, d in regime["per_state"].items():
        ev_str = "(evidential)" if d["is_evidential"] else "(non-evidential)"
        stress_note = _ALWAYS_NON_EVIDENTIAL_NOTES.get(state, "") if d["is_stress_non_evidential"] else ""
        g = d["gap_q5q1"]
        print(f"  {state}: n={d['n_total_quintile_valid']}, Q5-Q1 gap={f'{g:.2f}pp' if g is not None else 'N/A'} {ev_str}{stress_note}")
    if regime["regime_carried"]:
        print(f"  ** REGIME-CARRIED: edge concentrated in {regime['regime_carried_state']} only (annotation, does NOT demote verdict) **")
    print(f"\nFDR (correcting for testing three hypotheses at once, q=10%):")
    for h_name, h_info in result["fdr_report"].items():
        rej = "REJECTED" if h_info["rejected"] else "not rejected"
        print(f"  {h_name}: p_raw={h_info['p_raw']:.4f}, p_adj={h_info['p_adj']:.4f} → {rej}")
    print(f"\n{'=' * 72}")
    print(f"EXPLORE DECISION: {result['explore_decision']}")
    rat = result["explore_decision_rationale"]
    print(f"  gap>0: {rat['gap_positive']}  ρ_s>0: {rat['rho_positive']}  "
          f"band_stable: {rat['band_sign_stable']}  MDE_ok: {rat['mde_gate_passed']}")
    print("=" * 72 + "\n")
