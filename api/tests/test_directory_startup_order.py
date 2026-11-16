"""Startup must reconcile orphaned runs before the scheduler can start new ones."""
from __future__ import annotations

import pytest

from akashic import main, scheduler
from akashic.services import maintenance_jobs
from akashic.services.directory import sync


@pytest.mark.asyncio
async def test_reconcile_runs_before_scheduler_starts(monkeypatch):
    calls: list[str] = []

    async def jobs():
        calls.append("maintenance")

    async def runs():
        calls.append("directory")
        return 0
    monkeypatch.setattr(maintenance_jobs, "reconcile_orphans", jobs)
    monkeypatch.setattr(sync, "reconcile_directory_runs", runs)
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: calls.append("scheduler"))

    await main._recover_orphans_then_start_scheduler()
    assert calls == ["maintenance", "directory", "scheduler"]


@pytest.mark.asyncio
async def test_scheduler_starts_even_if_reconcile_fails(monkeypatch):
    calls: list[str] = []

    async def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(maintenance_jobs, "reconcile_orphans", boom)
    monkeypatch.setattr(sync, "reconcile_directory_runs", boom)
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: calls.append("scheduler"))
    await main._recover_orphans_then_start_scheduler()
    assert calls == ["scheduler"]
