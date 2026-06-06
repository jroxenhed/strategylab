"""F336: Manual LRU eviction + staleness audit for the price-frame cache.

Operates on backend/data/turnaround/price_cache/v1/ (or the version directory
configured by _PRICE_CACHE_VERSION in turnaround_validation.py).

Nothing in the normal read/write path calls this script — eviction is a manual
decision because the cache rebuild cost is ~35 min of sequential yahoo fetches.
Run it when disk space is the constraint (cache is 3.1GB+ today).

Usage:
    python3 backend/scripts/prune_price_cache.py [options]

Options:
    --max-gb N       Evict oldest files when total cache size exceeds N GB.
                     Default: 2.0 GB.
    --target-gb N    Prune down to N GB after eviction (must be <= --max-gb).
                     Default: 1.0 GB.
    --dry-run        Print what would be evicted/flagged; do not delete anything.
    --audit          Fingerprint-check N random cached frames by reloading them
                     from disk and comparing their adjusted-close fingerprint
                     against the value computed at read time.  Stale frames are
                     reported; with --fix they are also evicted.
    --audit-n N      Number of random frames to audit (default: 100).
    --fix            Combined with --audit: evict frames whose fingerprint no
                     longer matches (simulating a post-split re-adjustment).
                     Has no effect without --audit.
    --cache-dir DIR  Override the cache directory (default: auto-detected from
                     turnaround_validation._PRICE_CACHE_DIR).

Exits 0 on success, 1 on error.

Examples:
    # Dry-run: show what a 2GB→1GB prune would delete:
    python3 backend/scripts/prune_price_cache.py --dry-run

    # Actually prune to 1.5GB:
    python3 backend/scripts/prune_price_cache.py --max-gb 2.0 --target-gb 1.5

    # Audit 200 random frames for staleness without deleting anything:
    python3 backend/scripts/prune_price_cache.py --audit --audit-n 200 --dry-run

    # Audit and fix stale frames:
    python3 backend/scripts/prune_price_cache.py --audit --fix
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path setup — allow running from repo root or from backend/
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (_BACKEND_DIR, str(_REPO_ROOT)):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _get_cache_version_dir(cache_dir_override: Optional[str] = None) -> Path:
    """Return the versioned cache directory (price_cache/v1/ by default)."""
    try:
        import turnaround_validation as tv
        if cache_dir_override:
            base = Path(cache_dir_override)
        else:
            base = tv._PRICE_CACHE_DIR
        version = tv._PRICE_CACHE_VERSION
        return base / version
    except ImportError as exc:
        log.error("Cannot import turnaround_validation: %s", exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Core: LRU eviction
# ---------------------------------------------------------------------------

_PRUNE_FLOOR_BYTES = int(0.5 * 1024 ** 3)  # 0.5 GB — refuse to evict below this without --force


def _lru_evict(
    version_dir: Path,
    max_bytes: int,
    target_bytes: int,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, list[Path]]:
    """Evict oldest files (by mtime) until total size <= target_bytes.

    Args:
        version_dir: The versioned cache directory to scan (e.g., price_cache/v1/).
        max_bytes:   Evict when total size is >= this threshold (at or over limit).
                     Without --force, targets are clamped to the 0.5 GB floor and
                     the 50%-of-cache eviction cap (REL-05); --force lifts both.
        target_bytes: Prune until total size drops to this value or below.
        dry_run:     If True, print intended actions without deleting.

    Returns:
        (bytes_freed, evicted_file_paths)
    """
    if not version_dir.exists():
        log.info("Cache directory does not exist: %s", version_dir)
        return 0, []

    pkl_files = sorted(
        version_dir.glob("*.pkl"),
        key=lambda p: p.stat().st_mtime,  # oldest first
    )
    if not pkl_files:
        log.info("No .pkl files found in %s", version_dir)
        return 0, []

    total_size = sum(p.stat().st_size for p in pkl_files)
    total_gb = total_size / (1024 ** 3)
    log.info(
        "Cache: %d files, %.2f GB in %s",
        len(pkl_files), total_gb, version_dir,
    )

    if total_size < max_bytes:
        log.info(
            "Cache size %.2f GB is within limit %.2f GB — nothing to evict.",
            total_gb, max_bytes / (1024 ** 3),
        )
        return 0, []

    # REL-05: floor guard — refuse to evict below 0.5 GB or >50% of cache
    # without --force, to protect against operator typos wiping the cache.
    if not force:
        effective_target = max(target_bytes, _PRUNE_FLOOR_BYTES)
        max_evict_bytes = total_size // 2  # 50% cap
        safe_target = max(effective_target, total_size - max_evict_bytes)
        if safe_target > target_bytes:
            saved_bytes = safe_target - target_bytes
            log.warning(
                "REL-05 floor guard: requested target %.2f GB would evict more than 50%% of cache "
                "or drop below the 0.5 GB floor. Clamping target to %.2f GB "
                "(saved %.1f MB from deletion). Pass --force to override.",
                target_bytes / (1024 ** 3),
                safe_target / (1024 ** 3),
                saved_bytes / (1024 ** 2),
            )
            target_bytes = safe_target

    evicted: list[Path] = []
    freed = 0
    remaining_size = total_size
    files = list(pkl_files)  # mutable copy (oldest first)

    # REL-06: deletion manifest — written (appended) before each unlink so an
    # operator can reconstruct what was deleted even if the process dies mid-run.
    # Dry-run writes the same list to manifest so the output is identical in both modes.
    import datetime as _dt
    manifest_path = version_dir / "eviction_manifest.log"
    manifest_header = (
        f"# eviction run {_dt.datetime.now().isoformat()} "
        f"{'[DRY-RUN]' if dry_run else '[LIVE]'}\n"
    )

    def _manifest_append(line: str) -> None:
        try:
            with open(manifest_path, "a", encoding="utf-8") as mf:
                mf.write(line + "\n")
        except OSError as me:
            log.warning("manifest write failed: %s", me)

    _manifest_append(manifest_header.rstrip())

    while remaining_size > target_bytes and files:
        p = files.pop(0)
        try:
            size = p.stat().st_size
        except OSError:
            continue  # file already gone

        manifest_line = f"{'DRY' if dry_run else 'DEL'} {p} {size}"
        _manifest_append(manifest_line)

        if dry_run:
            log.info("[DRY-RUN] would evict: %s (%.1f KB)", p.name, size / 1024)
            evicted.append(p)
            freed += size
            remaining_size -= size
        else:
            try:
                p.unlink()
                evicted.append(p)
                freed += size
                remaining_size -= size
                log.info("evicted: %s (%.1f KB freed)", p.name, size / 1024)
            except OSError as exc:
                log.warning("failed to unlink %s: %s", p, exc)

    action = "Would free" if dry_run else "Freed"
    log.info(
        "%s %.2f MB across %d files; remaining cache: %.2f GB",
        action, freed / (1024 ** 2), len(evicted),
        remaining_size / (1024 ** 3),
    )
    log.info("Manifest written to: %s", manifest_path)
    return freed, evicted


# ---------------------------------------------------------------------------
# Staleness audit (F336 fingerprint path)
# ---------------------------------------------------------------------------

def _run_audit(
    version_dir: Path,
    n: int,
    fix: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """Audit N random cached frames for ON-DISK CORRUPTION via fingerprint stability.

    NOTE: this audit does NOT detect live split staleness.  Two reads of the
    same file produce the same fingerprint regardless of whether the underlying
    data has been split-adjusted since caching.  To purge split-stale frames,
    bump _PRICE_CACHE_VERSION in turnaround_validation.py (blunt-force evict).

    The audit path reloads each sampled frame twice and compares fingerprints.
    Mismatch means the file is being written concurrently (race condition) or
    is genuinely corrupt (truncation, partial write).  Both cases are reported
    and evicted with --fix.

    Returns:
        (audited_count, stale_or_corrupt_count)
    """
    try:
        import turnaround_validation as tv
    except ImportError as exc:
        log.error("Cannot import turnaround_validation for audit: %s", exc)
        return 0, 0

    if not version_dir.exists():
        log.info("Audit: cache directory does not exist: %s", version_dir)
        return 0, 0

    pkl_files = list(version_dir.glob("*.pkl"))
    if not pkl_files:
        log.info("Audit: no .pkl files found")
        return 0, 0

    sample = random.sample(pkl_files, min(n, len(pkl_files)))
    log.info("Audit: sampling %d/%d files for fingerprint stability", len(sample), len(pkl_files))

    # REL-06: deletion manifest for audit --fix path.
    import datetime as _dt
    manifest_path = version_dir / "audit_manifest.log"

    def _audit_manifest_append(line: str) -> None:
        try:
            with open(manifest_path, "a", encoding="utf-8") as mf:
                mf.write(line + "\n")
        except OSError as me:
            log.warning("audit manifest write failed: %s", me)

    if fix:
        _audit_manifest_append(
            f"# audit run {_dt.datetime.now().isoformat()} "
            f"{'[DRY-RUN]' if dry_run else '[LIVE]'}"
        )

    stale_count = 0
    for p in sample:
        # Load the frame twice; fingerprints must match.
        try:
            import pickle
            with open(p, "rb") as fh:
                df1 = pickle.load(fh)
            with open(p, "rb") as fh:
                df2 = pickle.load(fh)
        except Exception as exc:
            log.warning("Audit: corrupt file %s: %s", p.name, exc)
            stale_count += 1
            if fix and not dry_run:
                _audit_manifest_append(f"DEL {p} corrupt")
                try:
                    p.unlink()
                    log.info("Audit --fix: evicted corrupt %s", p.name)
                except OSError as ue:
                    log.warning("Audit --fix: failed to evict %s: %s", p.name, ue)
            elif dry_run and fix:
                _audit_manifest_append(f"DRY {p} corrupt")
                log.info("[DRY-RUN] Audit --fix would evict corrupt: %s", p.name)
            continue

        import pandas as pd
        if not isinstance(df1, pd.DataFrame) or not isinstance(df2, pd.DataFrame):
            log.warning("Audit: non-DataFrame content in %s", p.name)
            stale_count += 1
            continue

        fp1 = tv._adjusted_close_fingerprint(df1)
        fp2 = tv._adjusted_close_fingerprint(df2)

        if fp1 != fp2:
            log.warning(
                "Audit: UNSTABLE fingerprint for %s (fp1=%s fp2=%s) — possible corruption",
                p.name, fp1, fp2,
            )
            stale_count += 1
            if fix and not dry_run:
                _audit_manifest_append(f"DEL {p} unstable fp1={fp1} fp2={fp2}")
                try:
                    p.unlink()
                    log.info("Audit --fix: evicted unstable %s", p.name)
                except OSError as ue:
                    log.warning("Audit --fix: failed to evict %s: %s", p.name, ue)
            elif fix and dry_run:
                _audit_manifest_append(f"DRY {p} unstable fp1={fp1} fp2={fp2}")
                log.info("[DRY-RUN] Audit --fix would evict: %s", p.name)
        else:
            log.debug("Audit: OK %s (fp=%s)", p.name, fp1)

    log.info(
        "Audit complete: %d/%d files checked, %d stale/corrupt",
        len(sample), len(pkl_files), stale_count,
    )
    return len(sample), stale_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prune and/or audit the StrategyLab price-frame cache (F336)."
    )
    parser.add_argument("--max-gb", type=float, default=2.0,
                        help="Evict when cache is >= this many GB (at or over limit; default: 2.0)")
    parser.add_argument("--target-gb", type=float, default=1.0,
                        help="Prune to this many GB (default: 1.0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done; do not delete anything")
    parser.add_argument("--audit", action="store_true",
                        help="Fingerprint-check N random cached frames for corruption")
    parser.add_argument("--audit-n", type=int, default=100,
                        help="Number of frames to audit (default: 100)")
    parser.add_argument("--fix", action="store_true",
                        help="With --audit: evict frames with unstable fingerprints")
    parser.add_argument("--cache-dir", type=str, default=None,
                        help="Override cache directory (default: auto-detected)")
    parser.add_argument("--force", action="store_true",
                        help="Bypass the 0.5 GB floor and 50%% cap safety guards")
    args = parser.parse_args()

    if args.target_gb > args.max_gb:
        log.error("--target-gb (%.1f) must be <= --max-gb (%.1f)", args.target_gb, args.max_gb)
        return 1

    version_dir = _get_cache_version_dir(args.cache_dir)
    log.info("Cache version dir: %s", version_dir)

    exit_code = 0

    # --- LRU eviction pass ---
    max_bytes = int(args.max_gb * 1024 ** 3)
    target_bytes = int(args.target_gb * 1024 ** 3)
    _lru_evict(version_dir, max_bytes=max_bytes, target_bytes=target_bytes, dry_run=args.dry_run, force=args.force)

    # --- Staleness audit pass ---
    if args.audit:
        audited, stale = _run_audit(
            version_dir, n=args.audit_n, fix=args.fix, dry_run=args.dry_run
        )
        if stale > 0 and not args.fix:
            log.warning(
                "Audit found %d stale/corrupt files. Re-run with --fix to evict them.", stale
            )
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
