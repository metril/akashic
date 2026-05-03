"""Pure-function tests for the v0.4.11 Phase 3 change-detection
broadcaster (api/akashic/services/scan_broadcast.py).

Covers `adaptive_threshold` only — `should_broadcast` and
`record_broadcast` need a Redis fixture; those live in the
pytest-redis-backed integration tests (existing conftest spins
one up alongside the asyncpg fixture).
"""
from akashic.services.scan_broadcast import (
    DELTA_CEILING,
    DELTA_FLOOR,
    adaptive_threshold,
)


def test_adaptive_threshold_floor_for_zero_total():
    # No estimated total yet (start of scan / pre-prewalk) — floor wins.
    assert adaptive_threshold(0) == DELTA_FLOOR
    assert adaptive_threshold(None) == DELTA_FLOOR


def test_adaptive_threshold_floor_for_small_total():
    # 4900 / 100 = 49 → below floor → floor wins.
    assert adaptive_threshold(4900) == DELTA_FLOOR


def test_adaptive_threshold_proportional_in_band():
    # Within (floor, ceiling) range — value is total / 100.
    assert adaptive_threshold(50_000) == 500
    assert adaptive_threshold(123_400) == 1234


def test_adaptive_threshold_ceiling_for_huge_total():
    # 100M / 100 = 1M → above ceiling → ceiling wins.
    assert adaptive_threshold(100_000_000) == DELTA_CEILING
    assert adaptive_threshold(10**12) == DELTA_CEILING


def test_adaptive_threshold_at_band_edges():
    # Exactly at the floor boundary.
    assert adaptive_threshold(DELTA_FLOOR * 100) == DELTA_FLOOR
    # Exactly at the ceiling boundary.
    assert adaptive_threshold(DELTA_CEILING * 100) == DELTA_CEILING
