"""Tests for F408: --append mode in news_gkg_ingest.build_panel().

All tests are unit-level — NO real BigQuery queries are executed.
The BigQuery boundary (_bq_query + download_table) is bypassed via monkeypatching
and dry_run_only=True.  Tests verify:
1. Month-chunk enumeration (_month_chunks)
2. Gap-day computation (_compute_gap_days)
3. Append flow: sidecar reading, start-date adjustment, chunk selection
4. Merge + dedupe logic (_finalize_panel)
5. Sidecar update: coverage_end, n_rows, n_tickers, gap_days recalculated
6. No-op when coverage already up-to-date
7. Missing sidecar raises FileNotFoundError
8. Dry-run append: returns None, no parquet written
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))

from research.news_gkg_ingest import _month_chunks, _compute_gap_days, _finalize_panel


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

def _make_panel_df(rows: list[tuple]) -> pd.DataFrame:
    """Build a minimal panel DataFrame from (ticker, date, alias_type, n, tone) tuples."""
    return pd.DataFrame([
        {"ticker": t, "date": d, "alias_type": at, "n_articles": n, "avg_tone": tone}
        for t, d, at, n, tone in rows
    ])


def _write_sidecar(output_dir: Path, coverage_end: date, coverage_start: date | None = None,
                   n_rows: int = 10, n_tickers: int = 3) -> None:
    meta = {
        "source": "test",
        "item": "F405",
        "fetch_vintage": "2026-05-01T00:00:00+00:00",
        "survivorship": "test",
        "coverage_start": str(coverage_start or date(2026, 1, 1)),
        "coverage_end": str(coverage_end),
        "pit_field": "publication date",
        "n_rows": n_rows,
        "n_tickers": n_tickers,
        "mapping_method": "test mapping",
        "honesty_note": "test note",
        "bytes_processed_gb": 315.0,
        "coverage_gap_days": [],
        "coverage_gap_note": "test",
    }
    (output_dir / "news_panel_gkg.meta.json").write_text(json.dumps(meta))


def _write_existing_panel(output_dir: Path, rows: list[tuple]) -> None:
    df = _make_panel_df(rows)
    df.to_parquet(output_dir / "news_panel_gkg.parquet", index=False)


# ---------------------------------------------------------------------------
# Tests: _month_chunks
# ---------------------------------------------------------------------------

class TestMonthChunks:
    def test_single_month(self) -> None:
        chunks = _month_chunks(date(2026, 5, 1), date(2026, 6, 1))
        assert len(chunks) == 1
        c_start, c_end, label = chunks[0]
        assert c_start == date(2026, 5, 1)
        assert c_end == date(2026, 6, 1)
        assert label == "panel_202605"

    def test_three_months(self) -> None:
        chunks = _month_chunks(date(2026, 3, 1), date(2026, 6, 1))
        assert len(chunks) == 3
        assert chunks[0][0] == date(2026, 3, 1)
        assert chunks[2][1] == date(2026, 6, 1)

    def test_partial_first_month(self) -> None:
        """start mid-month: first chunk starts at start, not month boundary."""
        chunks = _month_chunks(date(2026, 5, 16), date(2026, 7, 1))
        assert chunks[0][0] == date(2026, 5, 16)
        assert len(chunks) == 2  # May-partial + Jun

    def test_empty_range(self) -> None:
        chunks = _month_chunks(date(2026, 6, 1), date(2026, 6, 1))
        assert chunks == []

    def test_reversed_range_empty(self) -> None:
        chunks = _month_chunks(date(2026, 6, 2), date(2026, 6, 1))
        assert chunks == []

    def test_labels_unique(self) -> None:
        chunks = _month_chunks(date(2026, 1, 1), date(2026, 12, 1))
        labels = [label for _, _, label in chunks]
        assert len(labels) == len(set(labels)), "chunk labels must be unique"


# ---------------------------------------------------------------------------
# Tests: _compute_gap_days
# ---------------------------------------------------------------------------

class TestComputeGapDays:
    def test_no_gaps(self) -> None:
        days = {date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)}
        assert _compute_gap_days(days) == []

    def test_single_gap(self) -> None:
        days = {date(2026, 5, 1), date(2026, 5, 3)}
        gaps = _compute_gap_days(days)
        assert gaps == [date(2026, 5, 2)]

    def test_multiple_gaps(self) -> None:
        days = {date(2026, 5, 1), date(2026, 5, 5)}
        gaps = _compute_gap_days(days)
        assert gaps == [date(2026, 5, 2), date(2026, 5, 3), date(2026, 5, 4)]

    def test_empty_set(self) -> None:
        assert _compute_gap_days(set()) == []

    def test_single_day_no_gaps(self) -> None:
        assert _compute_gap_days({date(2026, 5, 1)}) == []


# ---------------------------------------------------------------------------
# Tests: _finalize_panel
# ---------------------------------------------------------------------------

class TestFinalizePanel:
    def test_writes_parquet_and_sidecar(self, tmp_path: Path) -> None:
        rows = [
            ("AAPL", date(2026, 5, 1), "full", 5, 1.2),
            ("AAPL", date(2026, 5, 2), "full", 3, 0.8),
        ]
        panel = _make_panel_df(rows)
        out = _finalize_panel(panel, tmp_path, total_gb=1.5)
        assert out == tmp_path / "news_panel_gkg.parquet"
        assert (tmp_path / "news_panel_gkg.parquet").exists()
        assert (tmp_path / "news_panel_gkg.meta.json").exists()

    def test_deduplication_keeps_last(self, tmp_path: Path) -> None:
        """Duplicate (ticker, date, alias_type) rows: last wins."""
        rows = [
            ("AAPL", date(2026, 5, 1), "full", 5, 1.2),  # old row
            ("AAPL", date(2026, 5, 1), "full", 9, 2.5),  # newer row — should win
        ]
        panel = _make_panel_df(rows)
        _finalize_panel(panel, tmp_path, total_gb=0.1)

        written = pd.read_parquet(tmp_path / "news_panel_gkg.parquet")
        aapl_rows = written[(written.ticker == "AAPL") & (written.date == date(2026, 5, 1))]
        assert len(aapl_rows) == 1
        assert int(aapl_rows.iloc[0]["n_articles"]) == 9

    def test_sidecar_coverage_end(self, tmp_path: Path) -> None:
        rows = [
            ("AAPL", date(2026, 5, 1), "full", 5, 1.2),
            ("AAPL", date(2026, 5, 31), "full", 3, 0.5),
        ]
        panel = _make_panel_df(rows)
        _finalize_panel(panel, tmp_path, total_gb=0.1)

        meta = json.loads((tmp_path / "news_panel_gkg.meta.json").read_text())
        assert meta["coverage_end"] == "2026-05-31"
        assert meta["n_rows"] == 2
        assert meta["n_tickers"] == 1

    def test_sidecar_gap_days_computed(self, tmp_path: Path) -> None:
        """Gap days in sidecar reflect actual missing days."""
        rows = [
            ("AAPL", date(2026, 5, 1), "full", 5, 1.2),
            ("AAPL", date(2026, 5, 3), "full", 3, 0.5),  # gap on 2026-05-02
        ]
        panel = _make_panel_df(rows)
        _finalize_panel(panel, tmp_path, total_gb=0.1)

        meta = json.loads((tmp_path / "news_panel_gkg.meta.json").read_text())
        assert "2026-05-02" in meta["coverage_gap_days"]

    def test_existing_meta_preserved(self, tmp_path: Path) -> None:
        """Static fields from existing_meta are preserved in the updated sidecar."""
        existing_meta = {
            "source": "gdelt_gkg_bulk_bigquery",
            "item": "F405",
            "fetch_vintage": "2026-01-01T00:00:00+00:00",
            "survivorship": "my-survivorship-note",
            "coverage_start": "2026-01-01",
            "coverage_end": "2026-04-30",
            "pit_field": "publication date",
            "n_rows": 100,
            "n_tickers": 10,
            "mapping_method": "my-mapping",
            "honesty_note": "my-honesty-note",
            "bytes_processed_gb": 315.0,
            "coverage_gap_days": [],
            "coverage_gap_note": "my-gap-note",
        }
        rows = [("AAPL", date(2026, 5, 1), "full", 5, 1.2)]
        panel = _make_panel_df(rows)
        _finalize_panel(panel, tmp_path, total_gb=3.0, existing_meta=existing_meta)

        meta = json.loads((tmp_path / "news_panel_gkg.meta.json").read_text())
        assert meta["survivorship"] == "my-survivorship-note"
        assert meta["mapping_method"] == "my-mapping"
        assert meta["honesty_note"] == "my-honesty-note"
        # Dynamic fields are recalculated
        assert meta["coverage_end"] == "2026-05-01"
        assert meta["n_rows"] == 1


# ---------------------------------------------------------------------------
# Tests: build_panel append-mode flow (BigQuery mocked / dry-run)
# ---------------------------------------------------------------------------

class TestBuildPanelAppendMode:
    """Test append flow without any real BigQuery queries."""

    def test_missing_sidecar_raises(self, tmp_path: Path) -> None:
        """--append with no existing sidecar raises FileNotFoundError."""
        from research.news_gkg_ingest import build_panel
        with pytest.raises(FileNotFoundError, match="news_panel_gkg.meta.json"):
            build_panel(
                output_dir=tmp_path,
                manifest_path=tmp_path / "manifest.parquet",
                start=date(2026, 1, 1),
                end=date(2026, 6, 1),
                dry_run_only=True,
                append_mode=True,
            )

    def test_noop_when_already_current(self, tmp_path: Path) -> None:
        """Append when coverage_end >= end-1 day returns existing parquet path, no fetch."""
        from research.news_gkg_ingest import build_panel

        coverage_end = date(2026, 6, 1)
        end = date(2026, 5, 31)  # end <= coverage_end → nothing to do

        _write_sidecar(tmp_path, coverage_end)
        # Write a dummy parquet
        pd.DataFrame({"ticker": ["A"], "date": [date(2026, 1, 1)],
                      "alias_type": ["full"], "n_articles": [1], "avg_tone": [0.0]}
                     ).to_parquet(tmp_path / "news_panel_gkg.parquet", index=False)

        result = build_panel(
            output_dir=tmp_path,
            manifest_path=tmp_path / "manifest.parquet",
            start=date(2026, 1, 1),
            end=end,
            dry_run_only=False,
            append_mode=True,
        )
        # Parquet path returned, no error
        assert result == tmp_path / "news_panel_gkg.parquet"

    def test_dry_run_append_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """dry_run_only=True in append mode returns None and prints cost; no parquet written."""
        import research.news_gkg_ingest as mod

        coverage_end = date(2026, 5, 15)
        _write_sidecar(tmp_path, coverage_end)
        _write_existing_panel(tmp_path, [
            ("AAPL", date(2026, 5, 1), "full", 5, 1.2),
        ])

        # Mock the expensive parts to avoid real BQ calls
        monkeypatch.setattr(mod, "ensure_dataset", lambda: None)
        monkeypatch.setattr(mod, "build_alias_frame", lambda path: pd.DataFrame(
            {"ticker": ["AAPL"], "n_tickers_for_alias": [1], "alias": ["apple inc"], "alias_type": ["full"]}
        ))
        monkeypatch.setattr(mod, "upload_aliases", lambda *a, **kw: None)
        monkeypatch.setattr(mod, "dry_run_total_gb", lambda chunks: 2.8)

        result = mod.build_panel(
            output_dir=tmp_path,
            manifest_path=tmp_path / "manifest.parquet",
            start=date(2026, 1, 1),
            end=date(2026, 6, 15),
            dry_run_only=True,
            append_mode=True,
        )
        assert result is None

    def test_append_start_set_from_sidecar(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In append mode, build_panel uses coverage_end+1 as effective start."""
        import research.news_gkg_ingest as mod

        coverage_end = date(2026, 5, 15)
        _write_sidecar(tmp_path, coverage_end)
        _write_existing_panel(tmp_path, [
            ("AAPL", date(2026, 5, 1), "full", 5, 1.2),
        ])

        seen_chunks = []

        def _fake_dry_run(chunks):
            seen_chunks.extend(chunks)
            return 1.5

        monkeypatch.setattr(mod, "ensure_dataset", lambda: None)
        monkeypatch.setattr(mod, "build_alias_frame", lambda path: pd.DataFrame(
            {"ticker": ["AAPL"], "n_tickers_for_alias": [1], "alias": ["apple inc"], "alias_type": ["full"]}
        ))
        monkeypatch.setattr(mod, "upload_aliases", lambda *a, **kw: None)
        monkeypatch.setattr(mod, "dry_run_total_gb", _fake_dry_run)

        mod.build_panel(
            output_dir=tmp_path,
            manifest_path=tmp_path / "manifest.parquet",
            start=date(2026, 1, 1),  # should be overridden by coverage_end+1
            end=date(2026, 6, 15),
            dry_run_only=True,
            append_mode=True,
        )

        # All chunks should start at 2026-05-16 (coverage_end + 1 day) or later
        expected_start = coverage_end + timedelta(days=1)  # 2026-05-16
        for c_start, c_end, label in seen_chunks:
            assert c_start >= expected_start, (
                f"chunk start {c_start} < expected_start {expected_start}"
            )

    def test_append_uses_month_chunks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Append mode uses _month_chunks, not _year_chunks."""
        import research.news_gkg_ingest as mod

        coverage_end = date(2026, 4, 30)
        _write_sidecar(tmp_path, coverage_end)
        _write_existing_panel(tmp_path, [
            ("AAPL", date(2026, 4, 1), "full", 5, 1.2),
        ])

        seen_chunks = []

        def _fake_dry_run(chunks):
            seen_chunks.extend(chunks)
            return 3.0

        monkeypatch.setattr(mod, "ensure_dataset", lambda: None)
        monkeypatch.setattr(mod, "build_alias_frame", lambda path: pd.DataFrame(
            {"ticker": ["AAPL"], "n_tickers_for_alias": [1], "alias": ["apple inc"], "alias_type": ["full"]}
        ))
        monkeypatch.setattr(mod, "upload_aliases", lambda *a, **kw: None)
        monkeypatch.setattr(mod, "dry_run_total_gb", _fake_dry_run)

        mod.build_panel(
            output_dir=tmp_path,
            manifest_path=tmp_path / "manifest.parquet",
            start=date(2026, 1, 1),
            end=date(2026, 7, 1),
            dry_run_only=True,
            append_mode=True,
        )

        # Should be month-chunks (labels like panel_202605) not year-chunks (panel_2026)
        for _, _, label in seen_chunks:
            assert len(label) == len("panel_202605"), (
                f"Expected month-chunk label (8 chars), got {label!r}"
            )

    def test_merge_and_sidecar_update(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Full append: existing + new rows merged, sidecar updated with new coverage_end."""
        import research.news_gkg_ingest as mod

        coverage_end = date(2026, 5, 31)
        _write_sidecar(tmp_path, coverage_end, coverage_start=date(2026, 5, 1))

        existing_rows = [
            ("AAPL", date(2026, 5, 1), "full", 5, 1.2),
            ("AAPL", date(2026, 5, 31), "full", 3, 0.5),
        ]
        _write_existing_panel(tmp_path, existing_rows)

        # KP-09: 'day' is the BigQuery column name returned by download_table().
        # build_panel renames it to 'date' after concat:
        #   new_panel["date"] = pd.to_datetime(new_panel.pop("day")).dt.date
        # This mock correctly mirrors the download_table() contract.  The column-name
        # assertion below verifies the rename was applied before _finalize_panel writes.
        new_rows_df = pd.DataFrame([
            {"ticker": "AAPL", "day": "2026-06-02", "alias_type": "full",
             "n_articles": 7, "avg_tone": 2.1},
            {"ticker": "AAPL", "day": "2026-06-03", "alias_type": "full",
             "n_articles": 4, "avg_tone": 1.0},
        ])

        monkeypatch.setattr(mod, "ensure_dataset", lambda: None)
        monkeypatch.setattr(mod, "build_alias_frame", lambda path: pd.DataFrame(
            {"ticker": ["AAPL"], "n_tickers_for_alias": [1], "alias": ["apple inc"], "alias_type": ["full"]}
        ))
        monkeypatch.setattr(mod, "upload_aliases", lambda *a, **kw: None)
        monkeypatch.setattr(mod, "dry_run_total_gb", lambda chunks: 3.0)
        monkeypatch.setattr(mod, "_bq_query", lambda *a, **kw: "")
        monkeypatch.setattr(mod, "download_table", lambda table: new_rows_df)

        result = mod.build_panel(
            output_dir=tmp_path,
            manifest_path=tmp_path / "manifest.parquet",
            start=date(2026, 1, 1),
            end=date(2026, 6, 15),
            dry_run_only=False,
            append_mode=True,
        )

        assert result is not None
        written = pd.read_parquet(result)
        # KP-09: assert parquet uses 'date' (not 'day') — verifies the day→date rename
        # in build_panel happened before _finalize_panel wrote to disk.
        assert "date" in written.columns, (
            f"Written parquet must have 'date' column; got {list(written.columns)}"
        )
        assert "day" not in written.columns, (
            "'day' column must not appear in written parquet (should have been renamed to 'date')"
        )
        # Should have all 4 rows: 2 existing + 2 new
        assert len(written) == 4

        meta = json.loads((tmp_path / "news_panel_gkg.meta.json").read_text())
        assert meta["coverage_end"] == "2026-06-03"
        assert meta["n_rows"] == 4
