"""File utilities — orphan .tmp cleanup for atomic-write callers."""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_TMP_MAX_AGE_SECS = 3600  # 1 hour


def cleanup_orphan_tmps(directories: list[Path]) -> int:
    """Unlink *.tmp files older than 1 hour in each directory.

    Returns the count of files removed. Logs at INFO if any are found.
    Best-effort — OS errors are logged but don't propagate.
    """
    now = time.time()
    removed = 0
    for directory in directories:
        if not directory.exists():
            continue
        try:
            for entry in directory.glob("*.tmp"):
                if not entry.is_file():
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if now - mtime <= _TMP_MAX_AGE_SECS:
                    continue
                try:
                    entry.unlink()
                    removed += 1
                except OSError as e:
                    logger.warning("could not unlink orphan tmp %s: %s", entry, e)
        except OSError as e:
            logger.warning("could not scan %s for orphan tmps: %s", directory, e)
    if removed:
        logger.info("cleaned up %d orphan .tmp files", removed)
    return removed
