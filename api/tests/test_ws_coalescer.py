"""Unit tests for the per-WebSocket scan-event coalescer (v0.4.3).

The coalescer wraps each /ws/scans connection's egress so a busy
scan doesn't drown the browser in heartbeats. Tests target the
class directly — easier to control timing than threading through
a real WS roundtrip.
"""
from __future__ import annotations

import asyncio

import pytest

from akashic.routers.scan_websocket import _PerScanCoalescer


@pytest.mark.asyncio
async def test_burst_collapses_to_one_frame_per_window():
    sent: list[dict] = []

    async def _send(ev):
        sent.append(ev)

    # Tight 50ms window so the test runs fast.
    coalescer = _PerScanCoalescer(_send, interval=0.05)

    # 10 heartbeats for the same scan_id within ~10ms.
    for i in range(10):
        await coalescer.feed({
            "kind": "scan.state",
            "scan_id": "scan-A",
            "scan_status": "running",
            "files_found": i,
        })
    # Let the drain loop run.
    await asyncio.sleep(0.2)
    await coalescer.aclose()

    # First feed sends immediately (no last_sent yet); subsequent
    # heartbeats coalesce into the latest. Expect exactly one
    # delivered post-drain (with files_found=9, the latest).
    # NOTE: the first heartbeat may or may not flush immediately
    # depending on monotonic-clock resolution; what matters is
    # that we get << 10 frames AND the final delivered frame
    # carries the latest state.
    assert len(sent) <= 3, f"too many frames: {len(sent)} (sent: {sent})"
    assert sent[-1]["files_found"] == 9


@pytest.mark.asyncio
async def test_terminal_event_flushes_immediately():
    sent: list[dict] = []

    async def _send(ev):
        sent.append(ev)

    coalescer = _PerScanCoalescer(_send, interval=10.0)  # huge window

    await coalescer.feed({
        "kind": "scan.state", "scan_id": "scan-A",
        "scan_status": "running", "files_found": 1,
    })
    await coalescer.feed({
        "kind": "scan.state", "scan_id": "scan-A",
        "scan_status": "running", "files_found": 2,
    })
    # Terminal — must land NOW even with 10s coalesce window.
    await coalescer.feed({
        "kind": "scan.state", "scan_id": "scan-A",
        "scan_status": "completed", "files_found": 99,
    })

    # Find the completed frame in sent (terminal flushes immediately).
    terminals = [e for e in sent if e["scan_status"] == "completed"]
    assert len(terminals) == 1
    assert terminals[0]["files_found"] == 99
    await coalescer.aclose()


@pytest.mark.asyncio
async def test_non_scan_state_passes_through():
    sent: list[dict] = []

    async def _send(ev):
        sent.append(ev)

    coalescer = _PerScanCoalescer(_send, interval=0.5)

    # source.created / source.deleted / ping / error / snapshot all
    # bypass the coalescer — they're not heartbeats.
    pass_through = [
        {"kind": "source.created", "source_id": "src-1"},
        {"kind": "source.deleted", "source_id": "src-1"},
        {"kind": "ping"},
        {"kind": "error", "message": "x"},
    ]
    for ev in pass_through:
        await coalescer.feed(ev)
    await coalescer.aclose()

    assert sent == pass_through


@pytest.mark.asyncio
async def test_separate_scans_dont_share_window():
    sent: list[dict] = []

    async def _send(ev):
        sent.append(ev)

    coalescer = _PerScanCoalescer(_send, interval=0.05)

    # Two scans firing heartbeats independently. Each scan_id has
    # its own rate budget — A's heartbeats shouldn't block B's.
    await coalescer.feed({
        "kind": "scan.state", "scan_id": "scan-A",
        "scan_status": "running", "files_found": 1,
    })
    await coalescer.feed({
        "kind": "scan.state", "scan_id": "scan-B",
        "scan_status": "running", "files_found": 1,
    })
    await asyncio.sleep(0.2)
    await coalescer.aclose()

    by_scan = {e["scan_id"] for e in sent if e.get("kind") == "scan.state"}
    assert by_scan == {"scan-A", "scan-B"}
