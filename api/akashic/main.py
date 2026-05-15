import gzip
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from akashic.config import settings

from akashic.routers import users, ingest, hosts, sources, source_test, search, entries, entry_content, browse, duplicates, tags, analytics, purge, webhooks, scans, scan_progress, scan_websocket, scan_work, auth, effective_perms, identities, admin_audit, group_resolution, principals, access, dashboard, storage_explorer, scanners, scanner_discovery, server_settings, credential_profiles, source_oauth, health_services
from akashic.services import metrics as metrics_svc

logger = logging.getLogger(__name__)


# Slow-request observability + Prometheus instrumentation, both
# served by one middleware (Phase 6 + Phase 10 of v0.4.3). Every
# request gets observed for the metrics histogram; only requests
# beyond _REQUEST_SLOW_MS additionally hit the slow-log.
_REQUEST_SLOW_MS = 250
# Don't instrument the metrics endpoint itself — would self-emit
# every scrape, and /health is called constantly enough to dwarf
# real api traffic in the histogram.
_INSTRUMENT_SKIP_PATHS = frozenset({"/metrics", "/health"})


class _GzipRequestMiddleware:
    """ASGI middleware that decompresses ``Content-Encoding: gzip``
    request bodies before route handlers see them.

    v0.29.2 — the scanner agent's batch POSTs (5–500 KB JSON each) now
    arrive gzipped to cut wire size 5–10× on remote-scanner deployments.
    FastAPI/uvicorn don't decode request bodies automatically — Starlette's
    GZipMiddleware is response-side only — so this middleware bridges
    the gap.

    Implementation: wraps the ASGI ``receive`` callable, collects all
    ``http.request`` chunks until ``more_body=false``, decompresses the
    accumulated body, then yields it back as a single synthetic
    ``http.request`` event. Memory usage matches what FastAPI's
    ``request.body()`` would buffer anyway. The 32 MB cap on the
    decompressed size prevents a hostile sender from gzip-bombing the
    API (decompressed-size DoS).
    """

    _MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024  # 32 MB — matches nginx's body cap

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or []
        content_encoding = None
        for k, v in headers:
            if k == b"content-encoding":
                content_encoding = v.decode("latin-1").strip().lower()
                break
        if content_encoding != "gzip":
            await self.app(scope, receive, send)
            return

        # Drain the upstream request body, then synthesize one event.
        body_parts: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # http.disconnect or similar — pass it through.
                await self.app(scope, receive, send)
                return
            body_parts.append(message.get("body") or b"")
            if not message.get("more_body", False):
                break

        try:
            decompressed = gzip.decompress(b"".join(body_parts))
        except (OSError, EOFError) as exc:
            await Response(
                content=f"Invalid gzip body: {exc}".encode(),
                status_code=400,
            )(scope, receive, send)
            return
        if len(decompressed) > self._MAX_DECOMPRESSED_BYTES:
            await Response(
                content=b"Decompressed body exceeds 32 MB cap",
                status_code=413,
            )(scope, receive, send)
            return

        # Replace the encoding header so downstream code doesn't try to
        # decode again. Also drop content-length — it now reflects the
        # encoded length and is wrong for the decoded body.
        new_headers = []
        for k, v in headers:
            if k in (b"content-encoding", b"content-length"):
                continue
            new_headers.append((k, v))
        new_headers.append((b"content-length", str(len(decompressed)).encode("latin-1")))
        scope = dict(scope)
        scope["headers"] = new_headers

        sent = False

        async def _replay():
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": decompressed,
                    "more_body": False,
                }
            # Mirror what uvicorn would emit after the body: an empty
            # disconnect signal lets long-running consumers shut down
            # cleanly. Without this, code that loops on receive() would
            # block forever after consuming the synthetic body.
            return {"type": "http.disconnect"}

        await self.app(scope, _replay, send)


class _TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path_template = (
            request.scope.get("route").path
            if request.scope.get("route") is not None
            else request.url.path
        )
        if path_template in _INSTRUMENT_SKIP_PATHS:
            return await call_next(request)
        # Make the request path visible to the SQL slow-query listener
        # so per-endpoint thresholds (settings.slow_query_ms_overrides)
        # can apply.
        from akashic.database import current_endpoint_var
        token = current_endpoint_var.set(path_template)
        try:
            t0 = time.perf_counter()
            response = await call_next(request)
            dur_s = time.perf_counter() - t0
        finally:
            current_endpoint_var.reset(token)
        # Record metrics first — slow log is just diagnostic on top.
        metrics_svc.observe_http_request(
            request.method, path_template, response.status_code, dur_s,
        )
        if dur_s * 1000 >= _REQUEST_SLOW_MS:
            logger.warning(
                "slow request: %s %s → %s in %.0fms",
                request.method, path_template,
                response.status_code, dur_s * 1000,
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Import all models so Alembic's `target_metadata` (env.py) sees them.
    from akashic import models  # noqa: F401

    from akashic.database import ensure_schema
    await ensure_schema()

    # First-boot bootstrap: seed `discovery_enabled` from env if the
    # row doesn't exist yet. Runtime UI PATCHes win after that.
    from akashic.config import settings
    from akashic.database import async_session
    from akashic.services.server_settings import (
        KEY_DISCOVERY_ENABLED, seed_from_env_if_missing,
    )
    if settings.scanner_discovery_enabled is not None:
        try:
            async with async_session() as session:
                await seed_from_env_if_missing(
                    session, KEY_DISCOVERY_ENABLED,
                    bool(settings.scanner_discovery_enabled),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("discovery setting seed failed: %s", exc)

    try:
        from akashic.services.search import ensure_index
        await ensure_index()
        logger.info("Meilisearch index initialized")
    except Exception as e:
        logger.warning("Meilisearch not available at startup: %s", e)

    from akashic.scheduler import start_scheduler
    start_scheduler()
    logger.info("Scan scheduler started")

    # v0.4.11 Phase 8e — streaming top_children worker. No-op when
    # the feature flag (settings.streaming_topchildren) is False.
    from akashic.services import top_children_worker
    top_children_worker.start_worker()

    # v0.29.2 — Meilisearch debouncer. Drains per-source dirty sets
    # every tick so high-throughput scans produce a handful of Meili
    # batches rather than hundreds of per-batch tasks. The handle +
    # stop event are stashed on app.state so shutdown can cancel.
    import asyncio as _asyncio
    from akashic.services import meili_indexer
    app.state.meili_debouncer_stop = _asyncio.Event()
    app.state.meili_debouncer_task = _asyncio.create_task(
        meili_indexer.run_debouncer(app.state.meili_debouncer_stop)
    )

    yield

    # Shutdown
    from akashic.scheduler import stop_scheduler
    stop_scheduler()
    await top_children_worker.stop_worker()
    # Stop the Meili debouncer cleanly so an in-flight flush isn't
    # cancelled mid-call (which could leave the pending set partially
    # SPOPed without the index push completing).
    if hasattr(app.state, "meili_debouncer_stop"):
        app.state.meili_debouncer_stop.set()
        try:
            await app.state.meili_debouncer_task
        except Exception as exc:  # noqa: BLE001
            logger.warning("meili debouncer shutdown noise: %s", exc)
    from akashic.services import scan_pubsub
    await scan_pubsub.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Akashic", version="0.1.0", lifespan=lifespan)

    # CORS allow-list (review A-I4). Empty list (the default) means
    # CORSMiddleware isn't mounted at all — same-origin only, which
    # matches the typical deploy where the SPA + API share a host.
    # Set CORS_ALLOW_ORIGINS=["https://akashic.example.com"] (JSON
    # list in env) to enable cross-origin browser fetches with
    # credentials.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # Slow-request observability + Prometheus instrumentation.
    # Order matters: install before the routers so it wraps every
    # endpoint. `/health` and `/metrics` are excluded inside the
    # middleware (see _INSTRUMENT_SKIP_PATHS).
    app.add_middleware(_TimingMiddleware)

    # v0.29.2 — gzip request-body decoder. Added last so it sits
    # OUTSIDE _TimingMiddleware (last add_middleware = first to see
    # the request). The scanner agent gzip-encodes batch POSTs to
    # /api/ingest/batch; this middleware decompresses transparently
    # so the route handlers receive plain JSON regardless of whether
    # the client compressed.
    app.add_middleware(_GzipRequestMiddleware)

    # Liveness probe for compose healthchecks. Deliberately doesn't
    # touch the DB / Meili / Redis — we want this to flip green the
    # moment uvicorn is accepting connections, so dependent services
    # (the scanner) can wait via `depends_on: condition: service_healthy`
    # instead of racing the api on startup. A more thorough readiness
    # probe could check the downstream stack, but that belongs on a
    # separate /ready endpoint if and when we need it.
    @app.get("/health", include_in_schema=False)
    def health():
        return {"ok": True}

    # Prometheus scrape endpoint. Renders the global registry of
    # akashic_* metrics; meant to be polled by a Prometheus server
    # (see compose's `metrics` profile).
    @app.get("/metrics", include_in_schema=False)
    def metrics():
        body, content_type = metrics_svc.render_metrics()
        return Response(content=body, media_type=content_type)

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(ingest.router)
    app.include_router(hosts.router)
    app.include_router(credential_profiles.router)
    app.include_router(sources.router)
    app.include_router(source_test.router)
    app.include_router(search.router)
    app.include_router(entries.router)
    app.include_router(entry_content.router)
    app.include_router(browse.router)
    app.include_router(duplicates.router)
    app.include_router(tags.router)
    app.include_router(analytics.router)
    app.include_router(purge.router)
    app.include_router(webhooks.router)
    app.include_router(scans.router)
    app.include_router(scan_progress.router)
    app.include_router(scan_websocket.router)
    app.include_router(scan_work.router)
    app.include_router(effective_perms.router)
    app.include_router(identities.router)
    app.include_router(admin_audit.router)
    app.include_router(group_resolution.router)
    app.include_router(principals.router)
    app.include_router(access.router)
    app.include_router(dashboard.router)
    app.include_router(storage_explorer.router)
    app.include_router(scanners.router)
    app.include_router(scanner_discovery.router)
    app.include_router(server_settings.router)
    app.include_router(source_oauth.router)
    app.include_router(health_services.router)
    return app


app = create_app()
