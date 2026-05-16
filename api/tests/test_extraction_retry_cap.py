"""v0.29.8 — extraction RQ jobs ride a Retry(max=3) policy.

Pre-fix `_enqueue_extraction_jobs` did `q.enqueue(...)` with RQ's
default of unlimited retries, so a poison file (Tika 500, corrupt
PDF) would re-queue forever and `rq:queue:extraction` LLEN grew
without bound. After three attempts the job now lands in
`rq:failed`, surfacing under the System Status "Failed" card.

Uses the compose Redis but on an ISOLATED db index (15) so a live
`extraction-worker` container watching db 0 can't drain the test's
jobs before the assertions run — the test only inspects what
`_enqueue_extraction_jobs` produced, it never wants them executed.
"""
from __future__ import annotations

import pytest

from akashic.config import settings


def _isolated_redis_url() -> str:
    """settings.redis_url with the db index swapped to 15. The
    extraction-worker only watches db 0, so jobs enqueued here are
    never consumed mid-test."""
    base = settings.redis_url.rsplit("/", 1)[0]
    return f"{base}/15"


@pytest.fixture
def clean_extraction_queue():
    from redis import Redis
    url = _isolated_redis_url()
    sync = Redis.from_url(url)
    # Clear any stragglers before the test, then tear down after.
    sync.delete("rq:queue:extraction")
    yield url, sync
    job_ids = sync.lrange("rq:queue:extraction", 0, -1) or []
    sync.delete("rq:queue:extraction")
    for jid in job_ids:
        sync.delete(f"rq:job:{jid.decode() if isinstance(jid, bytes) else jid}")


def test_enqueue_extraction_jobs_sets_retry_policy(clean_extraction_queue):
    """Each enqueued job must carry retries_left=3 + the staggered
    interval schedule [10, 60, 300] s so transient Tika failures
    back off rather than re-queuing immediately."""
    from akashic.routers.ingest import _enqueue_extraction_jobs
    from rq import Queue
    from rq.job import Job

    url, sync = clean_extraction_queue
    _enqueue_extraction_jobs(["entry-1"], url)

    q = Queue("extraction", connection=sync)
    assert q.count == 1
    job_ids = q.get_job_ids()
    assert len(job_ids) == 1
    job = Job.fetch(job_ids[0], connection=sync)
    assert job.retries_left == 3, f"retries_left={job.retries_left}"
    assert job.retry_intervals == [10, 60, 300]


def test_enqueue_handles_multiple_entries(clean_extraction_queue):
    """Bulk enqueue of N entry IDs queues N jobs, each independently
    retry-capped."""
    from akashic.routers.ingest import _enqueue_extraction_jobs
    from rq import Queue
    from rq.job import Job

    url, sync = clean_extraction_queue
    _enqueue_extraction_jobs(["e1", "e2", "e3"], url)

    q = Queue("extraction", connection=sync)
    assert q.count == 3
    for jid in q.get_job_ids():
        job = Job.fetch(jid, connection=sync)
        assert job.retries_left == 3
