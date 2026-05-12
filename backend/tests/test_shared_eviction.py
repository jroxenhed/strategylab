"""F124 + F138 — cache + dedup-lock eviction tests for shared._fetch."""
import threading

import shared


def _reset_state() -> None:
    shared._fetch_cache.clear()
    shared._fetch_dedup_locks.clear()


def test_evict_cache_prunes_orphan_dedup_locks():
    """F124: locks whose dedup-key has no live cache entry are dropped."""
    _reset_state()
    for i in range(50):
        shared._fetch_dedup_locks[(f"SYM{i}", "1d", "yahoo", False)] = threading.Lock()
    assert len(shared._fetch_dedup_locks) == 50

    shared._evict_cache()
    assert shared._fetch_dedup_locks == {}


def test_evict_cache_keeps_locks_for_live_cache_entries():
    """Locks aligned with a cache entry survive eviction."""
    _reset_state()
    import time

    live_key = ("AAPL", "2024-01-01", "2024-02-01", "1d", "yahoo", False)
    shared._fetch_cache[live_key] = (time.monotonic(), None)
    shared._fetch_dedup_locks[("AAPL", "1d", "yahoo", False)] = threading.Lock()
    shared._fetch_dedup_locks[("MSFT", "1d", "yahoo", False)] = threading.Lock()

    shared._evict_cache()

    assert ("AAPL", "1d", "yahoo", False) in shared._fetch_dedup_locks
    assert ("MSFT", "1d", "yahoo", False) not in shared._fetch_dedup_locks


def test_dedup_locks_high_watermark_constant():
    """F138: watermark is a documented threshold, not a magic number."""
    assert shared._DEDUP_LOCKS_HIGH_WATERMARK == 200
    assert shared._DEDUP_LOCKS_HIGH_WATERMARK > shared._CACHE_MAX
