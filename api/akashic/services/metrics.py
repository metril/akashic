"""Prometheus metrics + slow-query/slow-request timing.

Three top-level metrics:

  - akashic_http_requests_total{method,path,status}  Counter
  - akashic_http_request_duration_seconds{method,path}  Histogram
  - akashic_pg_query_duration_seconds{operation}  Histogram

The middleware in main.py + the SQLAlchemy event hook in
database.py call into the `observe_*` helpers below; they're kept
here so both the slow-log path (Phase 6) and the metrics export
path (Phase 10) share a single source of truth for the
instrumentation.

Cardinality discipline:
- `path` MUST come from the FastAPI route template
  (`/api/sources/{source_id}`), NOT the literal URL — otherwise
  every UUID becomes its own time series and Prometheus melts.
- `operation` is the first SQL token, upper-cased (SELECT / UPDATE /
  …). Per-statement labels would be unbounded.
"""
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest,
)


http_requests_total = Counter(
    "akashic_http_requests_total",
    "HTTP requests by method, path template, and response status.",
    ["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
    "akashic_http_request_duration_seconds",
    "HTTP request latency (seconds), keyed by method + path template.",
    ["method", "path"],
    # Tuned for an api whose typical responses are < 100ms but where
    # an occasional analytics query can hit a few seconds.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

pg_query_duration_seconds = Histogram(
    "akashic_pg_query_duration_seconds",
    "Postgres query latency (seconds), keyed by SQL verb.",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)


def observe_http_request(method: str, path: str, status: int, duration_s: float) -> None:
    """Record one HTTP request. Called from the timing middleware."""
    http_requests_total.labels(method, path, str(status)).inc()
    http_request_duration_seconds.labels(method, path).observe(duration_s)


def observe_pg_query(statement: str, duration_s: float) -> None:
    """Record one Postgres query. Called from the SQLAlchemy hook
    in database.py for every executed statement."""
    operation = _operation_label(statement)
    pg_query_duration_seconds.labels(operation).observe(duration_s)


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST


def _operation_label(statement: str) -> str:
    """First whitespace-delimited token of the statement, uppercased.
    Falls back to `OTHER` if the statement is empty or doesn't start
    with a SQL keyword we recognise."""
    head = statement.lstrip().split(None, 1)
    if not head:
        return "OTHER"
    op = head[0].upper()
    # Constrain to a known set so a typo or weird construct can't
    # blow cardinality.
    if op in {"SELECT", "INSERT", "UPDATE", "DELETE", "BEGIN",
              "COMMIT", "ROLLBACK", "WITH", "CREATE", "DROP",
              "ALTER", "VALUES", "SHOW"}:
        return op
    return "OTHER"
