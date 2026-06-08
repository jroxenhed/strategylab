"""File utilities — atomic writes, file locking, and orphan .tmp cleanup."""

import contextlib
import fcntl
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

_LOCK_TIMEOUT_SECS = 30


@contextlib.contextmanager
def file_lock(path: "str | os.PathLike[str]", timeout: float = _LOCK_TIMEOUT_SECS) -> Generator[None, None, None]:
    """Exclusive inter-process file lock using ``<path>.lock``.

    Acquires an exclusive ``fcntl.flock`` on ``<path>.lock`` before yielding
    and releases it on exit.  The lock file is created if absent.  Uses a
    polling loop capped at *timeout* seconds; raises ``TimeoutError`` if the
    lock cannot be obtained within that time.

    Usage::

        with file_lock(ledger_path):
            data = json.loads(ledger_path.read_text())
            data.append(entry)
            atomic_write_text(ledger_path, json.dumps(data))

    POSIX only (uses ``fcntl``).  Designed for macOS + Linux / WSL.
    """
    lock_path = str(path) + ".lock"
    deadline = time.monotonic() + timeout
    # Ensure the lock file and its parent directory exist.
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    # B3 (PY-04/REL-02): open in 'a' (append/create) rather than 'w' (truncate)
    # so each acquisition does not clobber any content that might be written to
    # the lock file for debugging purposes (e.g. a PID stamp).  The lock file
    # sibling is intentionally left in place after release — unlinking inside the
    # with-block is a POSIX race (another process may have already re-opened the
    # inode); leaving it is correct.  The fd is held open for the duration of the
    # lock so the OS keeps the inode alive even if the filename is later unlinked.
    fd = open(lock_path, "a")  # noqa: WPS515 — kept open for duration of lock
    try:
        # Poll with exponential back-off until we get the lock or time out.
        delay = 0.01
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break  # lock acquired
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"file_lock: could not acquire lock on {lock_path!r} "
                        f"within {timeout}s"
                    ) from None
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.5)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()


def _rotate_backups(path: str, depth: int) -> None:
    """Rotate existing backups and copy the current file to <path>.bak.

    Depth 1: copies current file → <path>.bak (one backup kept).
    Depth N: rotates .bak.(N-1) → .bak.N, …, .bak → .bak.2, then copies
    current file → <path>.bak.

    Uses os.replace for the rotation steps (atomic rename on POSIX) and
    shutil.copy2 for the live-file copy so the original stays in place until
    the main write's os.replace.

    Trade-off: if this function raises (e.g. disk full), the caller logs a
    warning and continues — primary data write always wins over backup.
    """
    bak = path + ".bak"
    if depth == 1:
        shutil.copy2(path, bak)
        return
    # Rotate existing numbered backups from highest to lowest.
    for i in range(depth, 1, -1):
        src = bak if i == 2 else f"{bak}.{i - 1}"
        dst = f"{bak}.{i}"
        if os.path.exists(src):
            os.replace(src, dst)
    # Copy current file to .bak (depth ≥ 2 also uses plain .bak as slot 1).
    shutil.copy2(path, bak)


def atomic_write_text(
    path: "str | os.PathLike[str]",
    content: str,
    *,
    encoding: str = "utf-8",
    backup_depth: int = 1,
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

    Backup behaviour (backup_depth, default 1):
      - 0: no backups created.
      - 1: if the target already exists, copies it to <path>.bak before the
           rename.  On subsequent calls the .bak is overwritten (single backup).
      - N>1: rotates .bak → .bak.2 → … → .bak.N before copying current to .bak,
             keeping the N most-recent prior versions.

    Backup failure policy: if the backup copy raises (e.g. disk full, permission
    error), a WARNING is logged and the primary write proceeds normally.  The
    invariant is that a failed backup NEVER aborts the write — primary data
    integrity always takes priority over the backup copy.
    """
    if backup_depth < 0:
        raise ValueError(f"backup_depth must be >= 0, got {backup_depth}")

    path = str(path)
    dir_ = os.path.dirname(path) or "."

    # Rotate/copy backup BEFORE opening the temp file so the original is still
    # in place.  Failure is non-fatal: warn and continue.
    if backup_depth > 0 and os.path.exists(path):
        try:
            _rotate_backups(path, backup_depth)
        except OSError as exc:
            logger.warning(
                "atomic_write_text: backup failed for %s (depth=%d): %s — "
                "proceeding with primary write",
                path,
                backup_depth,
                exc,
            )

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
