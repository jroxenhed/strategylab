"""File utilities — atomic writes and orphan .tmp cleanup."""

import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_text(
    path: "str | os.PathLike[str]",
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Atomically replace *path* with *content*.

    Writes via NamedTemporaryFile in the SAME directory as the target so
    os.replace is rename-on-same-filesystem (atomic on POSIX).  Flushes Python
    buffers, calls os.fsync() before close so the data is durable across power
    loss between rename and writeback.  Best-effort cleanup of the temp file if
    rename never happens.  Never raises from cleanup paths.

    Durability invariant: if this function returns without raising, the content
    has been fsync'd to the target path.

    Cleanup invariant: if any step before os.replace raises, the temp file is
    unlinked (best-effort; OSError during unlink is silently ignored).
    """
    path = str(path)
    dir_ = os.path.dirname(path) or "."
    fd = tempfile.NamedTemporaryFile(
        mode="w", delete=False, dir=dir_, suffix=".tmp", encoding=encoding
    )
    try:
        fd.write(content)
        fd.flush()
        os.fsync(fd.fileno())
        # F83: fd.close() can itself raise (e.g. on a full disk during flush
        # finalisation).  Swallow the exception — the fsync above already made
        # the data durable, and os.replace still works on an open fd on POSIX.
        try:
            fd.close()
        except Exception:
            pass
        os.replace(fd.name, path)
    except Exception:
        # Best-effort cleanup: close then unlink.  Neither step should raise,
        # but guard anyway so the original exception is never masked.
        try:
            fd.close()
        except Exception:
            pass
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise


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
