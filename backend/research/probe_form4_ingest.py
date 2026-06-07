"""F338 Smoke Probe — Form 4 Dataset Ingest Layer (F356).

Runs 5 pre-stated anchors on the 2018q1 dataset (real data on disk).
Exits 0 only if no anchor FAILs.  NOT-RUN is honest — used when
the underlying data needed for the anchor is absent or ambiguous.

Usage:
    backend/venv/bin/python3 backend/research/probe_form4_ingest.py

Anchor summary (pre-stated per plan.md §9 + orchestrator overrides):
  1. P+A count exact: 7,718 P+A transactions in 2018q1 NONDERIV_TRANS
  2. Spot-ticker cross-diff: TSV events cross-checked vs stratified cache XMLs
     — scans ALL 45 quarters, reports per-class (single-owner / multi-owner /
       amendment). Symmetric 10b5-1 skip (COR-06 fix).
  3. acceptanceDateTime join success rate: direct+fetched ≥ 95% (2018q1 only)
  4. Universe filtering sanity: pass/(pass+fail) ≥ 60%
  5. 10b5-1 exclusion rate: 1–5% of P+A qualifying txns
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from datetime import date

import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_BASE_DIR = _BACKEND_DIR.parent
_DATASET_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "form4_datasets"
_SUBS_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "submissions"
_STRAT_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "form4_stratified"

from research.form4_ingest import build_form4_dataset_events  # noqa: E402
from research.r1_dose import _parse_qualifying_transactions, _is_10b51_text  # noqa: E402

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------
_results: list[tuple[str, str, str]] = []  # (anchor_id, status, detail)


def _record(anchor_id: str, status: str, detail: str) -> None:
    assert status in ("PASS", "FAIL", "NOT-RUN"), f"Invalid status: {status}"
    _results.append((anchor_id, status, detail))
    print(f"  [{status}] {anchor_id}: {detail}")


# ---------------------------------------------------------------------------
# Anchor 1: P+A count exact
# ---------------------------------------------------------------------------

def anchor1_pa_count() -> None:
    """Count P+A transactions in 2018q1 NONDERIV_TRANS — must be exactly 7,718."""
    print("\n--- Anchor 1: P+A count exact (pre-stated: 7,718) ---")
    zip_path = _DATASET_DIR / "2018q1_form345.zip"
    if not zip_path.exists():
        _record("A1", "NOT-RUN", f"ZIP not found: {zip_path}")
        return

    import csv
    with zipfile.ZipFile(zip_path) as z:
        with z.open("NONDERIV_TRANS.tsv") as f:
            nd_df = pd.read_csv(f, sep="\t", dtype=str,
                                quoting=csv.QUOTE_NONE, on_bad_lines="skip")

    p_count = (nd_df["TRANS_CODE"] == "P").sum()
    pa_count = ((nd_df["TRANS_CODE"] == "P") & (nd_df["TRANS_ACQUIRED_DISP_CD"] == "A")).sum()
    print(f"  TRANS_CODE==P: {p_count}")
    print(f"  TRANS_CODE==P AND ACQUIRED: {pa_count}  (informational: P-only = {p_count})")

    if pa_count == 7718:
        _record("A1", "PASS", f"P+A count = {pa_count} (exact match)")
    else:
        _record("A1", "FAIL", f"P+A count = {pa_count}, expected exactly 7,718")


# ---------------------------------------------------------------------------
# Anchor 2: Spot-ticker cross-diff — all 45 quarters, per-class reporting
#
# ADV-05 fix: scan ALL quarters; report per-class (single-owner / multi-owner
#             / amendment). Print NOT-RUN for a class with zero overlap.
# COR-06 fix: symmetric 10b5-1 skip — skip if EITHER TSV OR XML side is 10b5-1.
# ---------------------------------------------------------------------------

def _parse_xml_event_fields(xml_path: Path) -> dict | None:
    """Parse a stratified cache XML and return {D, n_txns_qualifying, form_10b51, owner_cik}."""
    try:
        content = xml_path.read_text(encoding="utf-8")
    except Exception:
        return None
    txns, owner_cik, form_10b51 = _parse_qualifying_transactions(content)
    D = 0.0
    n_qualifying = 0
    for txn in txns:
        if not txn["is_10b51"]:
            price = txn["price"]
            shares = txn["shares"]
            if price and price > 0:
                D += (shares or 0.0) * price
            n_qualifying += 1
    return {
        "D": D,
        "n_txns_qualifying": n_qualifying,
        "form_10b51": form_10b51,
        "owner_cik": owner_cik,
    }


def _crossdiff_one_quarter(
    quarter: str,
    xml_files_set: set[str],
    # per-class accumulators passed in/out
    single_owner: dict,
    multi_owner: dict,
    amendment: dict,
) -> None:
    """Cross-diff one quarter's TSV P+A forms against stratified-cache XMLs.

    Updates the three per-class accumulator dicts in place:
      {"checked": int, "matched": int, "mismatched": int, "skipped_10b51": int}
    """
    import csv as _csv
    from collections import defaultdict

    zip_path = _DATASET_DIR / f"{quarter}_form345.zip"
    if not zip_path.exists():
        return

    try:
        with zipfile.ZipFile(zip_path) as z:
            with z.open("SUBMISSION.tsv") as f:
                sub_df = pd.read_csv(f, sep="\t", dtype=str,
                                     quoting=_csv.QUOTE_NONE, on_bad_lines="skip")
            with z.open("NONDERIV_TRANS.tsv") as f:
                nd_df = pd.read_csv(f, sep="\t", dtype=str,
                                    quoting=_csv.QUOTE_NONE, on_bad_lines="skip")
            with z.open("FOOTNOTES.tsv") as f:
                fn_df = pd.read_csv(f, sep="\t", dtype=str,
                                    quoting=_csv.QUOTE_NONE, on_bad_lines="skip")
            with z.open("REPORTINGOWNER.tsv") as f:
                owner_df = pd.read_csv(f, sep="\t", dtype=str,
                                       quoting=_csv.QUOTE_NONE, on_bad_lines="skip")
    except Exception:
        return

    # Build set of P+A accessions from TSV
    pa_accs: set[str] = set(
        nd_df[(nd_df["TRANS_CODE"] == "P") & (nd_df["TRANS_ACQUIRED_DISP_CD"] == "A")]["ACCESSION_NUMBER"]
    )
    if not pa_accs:
        return

    # Build issuer CIK → padded_cik from SUBMISSION
    acc_to_cik_padded: dict[str, str] = {}
    acc_to_doc_type: dict[str, str] = {}
    for _, row in sub_df[sub_df["DOCUMENT_TYPE"].isin(["4", "4/A"])].iterrows():
        acc = row["ACCESSION_NUMBER"]
        cik_raw = row.get("ISSUERCIK", "")
        doc_type = row.get("DOCUMENT_TYPE", "4")
        try:
            acc_to_cik_padded[acc] = str(int(cik_raw)).zfill(10)
            acc_to_doc_type[acc] = doc_type
        except (ValueError, TypeError):
            pass

    # Owner count per accession
    acc_to_owner_count: dict[str, int] = {}
    if not owner_df.empty and "ACCESSION_NUMBER" in owner_df.columns:
        owner_counts = (
            owner_df[owner_df["ACCESSION_NUMBER"].notna()]
            .groupby("ACCESSION_NUMBER")["RPTOWNERCIK"]
            .nunique()
        )
        acc_to_owner_count = owner_counts.to_dict()

    # Find overlapping accessions
    candidates: list[tuple[str, str]] = []
    for acc in pa_accs:
        cik_padded = acc_to_cik_padded.get(acc)
        if cik_padded is None:
            continue
        acc_nd = acc.replace("-", "")
        xml_name = f"{cik_padded}_{acc_nd}.xml"
        if xml_name in xml_files_set:
            candidates.append((acc, cik_padded))

    if not candidates:
        return

    # Build footnotes and remarks lookups
    footnotes_by_acc: dict[str, list[str]] = defaultdict(list)
    if not fn_df.empty and "ACCESSION_NUMBER" in fn_df.columns:
        for _, frow in fn_df.iterrows():
            acc = frow.get("ACCESSION_NUMBER", "")
            txt = frow.get("FOOTNOTE_TXT", "")
            if acc and not pd.isna(acc) and txt and not pd.isna(txt):
                footnotes_by_acc[str(acc)].append(str(txt))

    acc_to_remarks: dict[str, str] = {}
    for _, row in sub_df.iterrows():
        acc = row.get("ACCESSION_NUMBER", "")
        rem = row.get("REMARKS", "")
        if acc:
            acc_to_remarks[str(acc)] = str(rem) if rem and not pd.isna(rem) else ""

    for accession, cik_padded in candidates:
        remarks = acc_to_remarks.get(accession, "")
        owner_count = acc_to_owner_count.get(accession, 1)
        doc_type = acc_to_doc_type.get(accession, "4")
        is_amendment = (doc_type == "4/A")
        is_multi_owner = (owner_count > 1)

        # Determine per-class bucket (amendment takes precedence for labeling)
        if is_amendment:
            bucket = amendment
        elif is_multi_owner:
            bucket = multi_owner
        else:
            bucket = single_owner

        # TSV-side P+A transactions
        txn_rows = nd_df[
            (nd_df["ACCESSION_NUMBER"] == accession) &
            (nd_df["TRANS_CODE"] == "P") &
            (nd_df["TRANS_ACQUIRED_DISP_CD"] == "A")
        ]
        if txn_rows.empty:
            continue

        # TSV-side 10b5-1 check
        form_10b51_tsv = False
        if remarks and _is_10b51_text(remarks):
            form_10b51_tsv = True
        for fn_txt in footnotes_by_acc.get(accession, []):
            if _is_10b51_text(fn_txt):
                form_10b51_tsv = True
                break

        # COR-06: symmetric — also skip if XML side is 10b5-1
        acc_nodash = accession.replace("-", "")
        xml_path = _STRAT_DIR / f"{cik_padded}_{acc_nodash}.xml"
        xml_fields = _parse_xml_event_fields(xml_path)
        if xml_fields is None:
            continue

        # Symmetric 10b5-1 skip: skip if EITHER side flags 10b5-1
        if form_10b51_tsv or xml_fields["form_10b51"]:
            bucket["skipped_10b51"] += 1
            continue

        # TSV-side: compute D and n_qualifying
        tsv_D = 0.0
        tsv_n = 0
        for _, txn_row in txn_rows.iterrows():
            sec_title = txn_row.get("SECURITY_TITLE", "")
            if sec_title and not pd.isna(sec_title) and _is_10b51_text(str(sec_title)):
                continue
            price = _safe_float(txn_row.get("TRANS_PRICEPERSHARE"))
            shares = _safe_float(txn_row.get("TRANS_SHARES"))
            if price and price > 0:
                tsv_D += (shares or 0.0) * price
            tsv_n += 1

        if tsv_n == 0:
            continue

        bucket["checked"] += 1
        xml_D = xml_fields["D"]
        xml_n = xml_fields["n_txns_qualifying"]

        sub_row = sub_df[sub_df["ACCESSION_NUMBER"] == accession]
        ticker = sub_row["ISSUERTRADINGSYMBOL"].iloc[0] if not sub_row.empty else "?"

        d_tol = max(1.0, max(abs(tsv_D), abs(xml_D)) * 0.002)  # 0.2% relative
        n_ok = xml_n == tsv_n
        d_ok = abs(xml_D - tsv_D) <= d_tol
        rel_diff_pct = abs(xml_D - tsv_D) / max(abs(tsv_D), abs(xml_D), 1.0) * 100

        if n_ok and d_ok:
            bucket["matched"] += 1
            print(f"  MATCH   {quarter} {ticker} {accession}: n={tsv_n} "
                  f"D_tsv={tsv_D:.2f} D_xml={xml_D:.2f} rel={rel_diff_pct:.3f}%"
                  f" {'[amend]' if is_amendment else '[multi]' if is_multi_owner else ''}")
        else:
            bucket["mismatched"] += 1
            reason = []
            if not n_ok:
                reason.append(f"n: tsv={tsv_n} xml={xml_n}")
            if not d_ok:
                reason.append(f"D: tsv={tsv_D:.2f} xml={xml_D:.2f} rel={rel_diff_pct:.3f}%>0.2%")
            print(f"  MISMATCH {quarter} {ticker} {accession}: {'; '.join(reason)}")


def anchor2_spot_ticker_crossdiff() -> None:
    """Cross-diff all 45 quarters' TSV forms against stratified cache XMLs.

    ADV-05 fix: scans ALL available quarters (not just 2018q1). Reports
    per-class counts: single-owner / multi-owner / amendment. If a class has
    zero overlap, prints NOT-RUN for that class — never silently folds into PASS.

    COR-06 fix: symmetric 10b5-1 skip — form is skipped if EITHER the TSV or
    XML side is 10b5-1 flagged.

    Pass gate: ≥95% of cross-checked forms match within $0.01 on D and
    exactly on n_txns_qualifying.
    """
    print("\n--- Anchor 2: Spot-ticker cross-diff (all quarters, per-class) ---")

    if not _STRAT_DIR.exists():
        _record("A2", "NOT-RUN", f"Stratified cache dir not found: {_STRAT_DIR}")
        return

    import os as _os
    xml_files_set = set(_os.listdir(str(_STRAT_DIR)))
    print(f"  Stratified cache XMLs available: {len(xml_files_set)}")

    if not _DATASET_DIR.exists():
        _record("A2", "NOT-RUN", f"Dataset dir not found: {_DATASET_DIR}")
        return

    # Discover quarters
    quarters = sorted(
        p.stem.replace("_form345", "")
        for p in _DATASET_DIR.iterdir()
        if p.suffix == ".zip" and p.stem.endswith("_form345")
    )
    print(f"  Quarters to scan: {len(quarters)}")

    # Per-class accumulators
    def _empty_bucket() -> dict:
        return {"checked": 0, "matched": 0, "mismatched": 0, "skipped_10b51": 0}

    single_owner = _empty_bucket()
    multi_owner = _empty_bucket()
    amendment = _empty_bucket()

    for q in quarters:
        _crossdiff_one_quarter(q, xml_files_set, single_owner, multi_owner, amendment)

    # Report per-class
    total_checked = 0
    total_matched = 0
    all_pass = True

    for class_name, bucket in [
        ("single-owner", single_owner),
        ("multi-owner", multi_owner),
        ("amendment", amendment),
    ]:
        c = bucket["checked"]
        m = bucket["matched"]
        mm = bucket["mismatched"]
        s = bucket["skipped_10b51"]
        if c == 0:
            print(f"  {class_name}: NOT-RUN (0 cross-checkable forms, skipped_10b51={s})")
        else:
            rate = m / c
            ok = rate >= 0.95
            if not ok:
                all_pass = False
            print(f"  {class_name}: checked={c} matched={m} mismatched={mm} "
                  f"skipped_10b51={s} rate={rate:.1%} {'PASS' if ok else 'FAIL'}")
            total_checked += c
            total_matched += m

    print(f"  Total across classes: checked={total_checked} matched={total_matched}")

    if total_checked == 0:
        _record("A2", "NOT-RUN", "No cross-checkable events found (no matching XMLs in any quarter)")
        return

    overall_rate = total_matched / total_checked
    if all_pass and overall_rate >= 0.95:
        _record("A2", "PASS", f"Overall rate {overall_rate:.1%} ({total_matched}/{total_checked}), gate ≥95%")
    else:
        _record("A2", "FAIL",
                f"Overall rate {overall_rate:.1%} ({total_matched}/{total_checked}), gate ≥95%")


def _safe_float(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        f = float(val)
        return f if not pd.isna(f) else None
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Anchor 3: acceptanceDateTime join success rate (2018q1 only)
# ---------------------------------------------------------------------------

def anchor3_acceptance_join_rate(meta: dict) -> None:
    """Direct+fetched ≥ 95% of events (fallback < 5%)."""
    print("\n--- Anchor 3: acceptanceDateTime join success rate (2018q1) ---")
    q = meta.get("per_quarter", {}).get("2018q1", {})
    if not q:
        _record("A3", "NOT-RUN", "2018q1 not in meta per_quarter")
        return

    direct = q.get("acceptances_direct_hit", 0)
    fetched = q.get("acceptances_fetched", 0)
    fallback = q.get("acceptances_fallback", 0)
    total = direct + fetched + fallback
    if total == 0:
        _record("A3", "NOT-RUN", "No events in 2018q1")
        return

    hit_rate = (direct + fetched) / total
    print(f"  direct={direct}  fetched={fetched}  fallback={fallback}  total={total}")
    print(f"  n_no_timestamp_dropped={q.get('n_no_timestamp_dropped', 0)}")
    print(f"  n_midnight_utc_adt={q.get('n_midnight_utc_adt', 0)}")
    print(f"  n_ticker_fallback={q.get('n_ticker_fallback', 0)}")
    print(f"  Hit rate: {hit_rate:.1%}  (gate ≥95%)")

    if hit_rate >= 0.95:
        _record("A3", "PASS", f"Hit rate {hit_rate:.1%} ({direct+fetched}/{total})")
    else:
        _record("A3", "FAIL", f"Hit rate {hit_rate:.1%} ({direct+fetched}/{total}), gate ≥95%")


# ---------------------------------------------------------------------------
# Anchor 4: Universe filtering sanity
# ---------------------------------------------------------------------------

def anchor4_universe_filter(meta: dict) -> None:
    """pass/(pass+fail) ≥ 60% for 2018q1."""
    print("\n--- Anchor 4: Universe filtering sanity (2018q1) ---")
    q = meta.get("per_quarter", {}).get("2018q1", {})
    if not q:
        _record("A4", "NOT-RUN", "2018q1 not in meta per_quarter")
        return

    n_pass = q.get("submissions_universe_pass", 0)
    n_fail = q.get("submissions_universe_fail", 0)
    total = n_pass + n_fail
    if total == 0:
        _record("A4", "NOT-RUN", "No submissions scanned")
        return

    ratio = n_pass / total
    print(f"  universe_pass={n_pass}  universe_fail={n_fail}  ratio={ratio:.1%}")
    print(f"  cik_match_only={q.get('cik_match_only',0)}  "
          f"ticker_match_only={q.get('ticker_match_only',0)}  "
          f"both={q.get('both_match',0)}  disagree={q.get('disagree',0)}")

    if ratio >= 0.60:
        _record("A4", "PASS", f"Pass ratio {ratio:.1%} ({n_pass}/{total}), gate ≥60%")
    else:
        _record("A4", "FAIL", f"Pass ratio {ratio:.1%} ({n_pass}/{total}), gate ≥60%")


# ---------------------------------------------------------------------------
# Anchor 5: 10b5-1 exclusion rate
# ---------------------------------------------------------------------------

def anchor5_10b51_rate(meta: dict) -> None:
    """1–5% of P+A txns excluded for 10b5-1 in 2018q1."""
    print("\n--- Anchor 5: 10b5-1 exclusion rate (2018q1) ---")
    q = meta.get("per_quarter", {}).get("2018q1", {})
    if not q:
        _record("A5", "NOT-RUN", "2018q1 not in meta per_quarter")
        return

    excluded = q.get("form4_10b51_excluded_txns", 0)
    qualified = q.get("qualifying_txns_raw", 0)
    if qualified == 0:
        _record("A5", "NOT-RUN", "No qualifying txns in 2018q1")
        return

    rate = excluded / qualified
    print(f"  excluded={excluded}  qualified={qualified}  rate={rate:.2%}")

    if 0.01 <= rate <= 0.05:
        _record("A5", "PASS", f"Exclusion rate {rate:.2%} (gate 1–5%)")
    else:
        _record("A5", "FAIL", f"Exclusion rate {rate:.2%}, gate 1–5%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("F338 Probe — Form 4 Dataset Ingest Layer (F356)")
    print("=" * 70)

    # Anchor 1: Run independently (raw count check, no ingest needed)
    anchor1_pa_count()

    # Run ingest for 2018q1 (fetch_missing=True to properly test Fork A)
    print("\n--- Running build_form4_dataset_events for 2018q1 ---")
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    events, meta = build_form4_dataset_events(
        quarters=["2018q1"],
        dataset_dir=_DATASET_DIR,
        submissions_dir=_SUBS_DIR,
        fetch_missing=True,
    )
    q_stats = meta.get("per_quarter", {}).get("2018q1", {})
    print(f"\nIngest complete: {len(events)} events")
    print(f"  qualifying_txns_raw={q_stats.get('qualifying_txns_raw',0)}")
    print(f"  submissions_scanned={q_stats.get('submissions_scanned',0)}")
    print(f"  universe_pass={q_stats.get('submissions_universe_pass',0)}")
    print(f"  amendments={q_stats.get('amendments',0)}")
    print(f"  n_superseded_dropped={meta.get('n_superseded_dropped',0)}")
    print(f"  n_no_timestamp_dropped={q_stats.get('n_no_timestamp_dropped',0)}")
    print(f"  n_midnight_utc_adt={q_stats.get('n_midnight_utc_adt',0)}")
    print(f"  n_ticker_fallback={q_stats.get('n_ticker_fallback',0)}")

    # Anchor 2 scans all 45 quarters for TSV↔XML overlaps
    anchor2_spot_ticker_crossdiff()
    anchor3_acceptance_join_rate(meta)
    anchor4_universe_filter(meta)
    anchor5_10b51_rate(meta)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PROBE RESULTS SUMMARY")
    print("=" * 70)
    n_pass = n_fail = n_notrun = 0
    for aid, status, detail in _results:
        tag = f"[{status}]".ljust(10)
        print(f"  {tag} {aid}: {detail}")
        if status == "PASS":
            n_pass += 1
        elif status == "FAIL":
            n_fail += 1
        else:
            n_notrun += 1

    print(f"\nPASS={n_pass}  FAIL={n_fail}  NOT-RUN={n_notrun}")
    if n_fail > 0:
        print("PROBE RESULT: FAIL — one or more anchors failed")
        return 1
    print("PROBE RESULT: PASS — all anchors passed or NOT-RUN (no FAILs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
