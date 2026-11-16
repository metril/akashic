import gzip
import logging
import time
import zlib
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware


from akashic.routers import users, ingest, hosts, sources, source_test, search, entries, entry_content, browse, duplicates, tags, analytics, purge, webhooks, scans, scan_progress, scan_websocket, scan_work, auth, scopes, api_tokens, effective_perms, identities, admin_audit, group_resolution, principals, access, dashboard, storage_explorer, scanners, scanner_protocol, scanner_discovery, server_settings, credential_profiles, source_oauth, health_services, maintenance
from akashic.errors import RequestIdMiddleware, error_response, install_error_handlers, request_id_of
from akashic.openapi import TAGS, app_version, install_openapi, operation_id
from akashic.services import metrics as metrics_svc
from akashic.versioning import API_PREFIX, WS_PREFIX, UnversionedPathMiddleware

logger = logging.getLogger(__name__)

_SWAGGER_DIR = Path(__file__).parent / "static" / "swagger"


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

        # Stream-decompress with a bounded output so a gzip bomb never
        # inflates past the cap in memory (gzip.decompress has no limit).
        too_big = False
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decompressed = d.decompress(
                b"".join(body_parts), self._MAX_DECOMPRESSED_BYTES + 1
            )
            if len(decompressed) > self._MAX_DECOMPRESSED_BYTES:
                too_big = True
            elif d.unconsumed_tail:
                too_big = True
            elif not d.eof:
                raise EOFError("truncated gzip stream")
        except (zlib.error, EOFError) as exc:
            await error_response(
                400, f"Invalid gzip body: {exc}", request_id_of(scope),
            )(scope, receive, send)
            return
        if too_big:
            await error_response(
                413, "Decompressed body exceeds 32 MB cap",
                request_id_of(scope),
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
        # so per-endpoint thresholds (slow_query_ms_overrides)
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


class _DynamicCORSMiddleware:
    """CORSMiddleware whose allow-list tracks the runtime snapshot."""

    def __init__(self, app):
        self.app = app
        self._cached: tuple | None = None
        self._inner = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from akashic.services.runtime_settings import rt
        origins = tuple(rt().cors_allow_origins or ())
        if not origins:
            return await self.app(scope, receive, send)
        if self._cached != origins:
            self._inner = CORSMiddleware(
                self.app, allow_origins=list(origins), allow_credentials=True,
                allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type"],
            )
            self._cached = origins
        return await self._inner(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Import all models so Alembic's `target_metadata` (env.py) sees them.
    from akashic import models  # noqa: F401

    from akashic.database import ensure_schema
    await ensure_schema()

    # One-time legacy env import, then load the runtime snapshot.
    from akashic.database import async_session
    from akashic.services.runtime_settings import load_snapshot
    from akashic.services.server_settings import import_legacy_env
    try:
        async with async_session() as session:
            await import_legacy_env(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("legacy env import failed: %s", exc)
    try:
        async with async_session() as session:
            await load_snapshot(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime settings load failed: %s", exc)

    try:
        from akashic.services.search import ensure_index
        await ensure_index()
        logger.info("Meilisearch index initialized")
    except Exception as e:
        logger.warning("Meilisearch not available at startup: %s", e)

    from akashic.scheduler import start_scheduler
    start_scheduler()
    logger.info("Scan scheduler started")

    # A maintenance job runs as an in-process task that does not survive
    # a restart — flip any row left `running` by the previous process to
    # `failed` so the Maintenance page doesn't show a phantom job.
    try:
        from akashic.services.maintenance_jobs import reconcile_orphans
        await reconcile_orphans()
    except Exception as exc:  # noqa: BLE001
        logger.warning("maintenance job reconcile failed: %s", exc)

    # v0.4.11 Phase 8e — streaming top_children worker. No-op when
    # the feature flag (streaming_topchildren) is False.
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
    from akashic.auth import oidc as _oidc
    await _oidc.aclose()
    # Background-task engines + shared webhook HTTP client.
    from akashic.services.post_scan import dispose_bg_engines
    from akashic.services import webhooks as _webhooks
    await meili_indexer.dispose_bg_engine()
    await dispose_bg_engines()
    await _webhooks.aclose()


def create_app() -> FastAPI:
    from akashic import routing

    routing.refresh_all()  # complete the static error-response derivation now every module is importable
    app = FastAPI(
        title="Akashic",
        version=app_version(),
        description="Universal file index: sources, scans, search, permissions and administration.",
        openapi_tags=TAGS,
        generate_unique_id_function=operation_id,
        docs_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    install_openapi(app)
    install_error_handlers(app)

    # CORS allow-list read from the runtime snapshot per request, so
    # changing origins in the UI needs no restart. Empty = same-origin.
    app.add_middleware(_DynamicCORSMiddleware)

    # Slow-request observability + Prometheus instrumentation.
    # Order matters: install before the routers so it wraps every
    # endpoint. `/health` and `/metrics` are excluded inside the
    # middleware (see _INSTRUMENT_SKIP_PATHS).
    app.add_middleware(_TimingMiddleware)

    # v0.29.2 — gzip request-body decoder. Added last so it sits
    # OUTSIDE _TimingMiddleware (last add_middleware = first to see
    # the request). The scanner agent gzip-encodes batch POSTs to
    # /api/v1/ingest/batch; this middleware decompresses transparently
    # so the route handlers receive plain JSON regardless of whether
    # the client compressed.
    app.add_middleware(_GzipRequestMiddleware)

    # Reject / redirect unversioned /api and /ws paths. Inside RequestId so
    # the rejection carries a request id.
    app.add_middleware(UnversionedPathMiddleware)

    # Outermost: every request (including gzip rejections) carries an id.
    app.add_middleware(RequestIdMiddleware)

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

    # Swagger UI, served from vendored assets: the web container's CSP is
    # `script-src 'self'`, so the default CDN + inline-script page is blocked.
    app.mount("/api/docs/assets", StaticFiles(directory=_SWAGGER_DIR), name="swagger-assets")

    @app.get("/api/docs", include_in_schema=False)
    def docs():
        return HTMLResponse(
            "<!doctype html><html><head><meta charset=\"utf-8\"><title>Akashic API</title>"
            "<link rel=\"stylesheet\" href=\"/api/docs/assets/swagger-ui.css\"></head>"
            "<body><div id=\"swagger-ui\"></div>"
            "<script src=\"/api/docs/assets/swagger-ui-bundle.js\"></script>"
            "<script src=\"/api/docs/assets/init.js\"></script></body></html>"
        )

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(scopes.router, prefix=API_PREFIX)
    app.include_router(api_tokens.me_router, prefix=API_PREFIX)
    app.include_router(api_tokens.admin_router, prefix=API_PREFIX)
    app.include_router(users.router, prefix=API_PREFIX)
    app.include_router(ingest.router, prefix=API_PREFIX)
    app.include_router(hosts.router, prefix=API_PREFIX)
    app.include_router(credential_profiles.router, prefix=API_PREFIX)
    app.include_router(sources.router, prefix=API_PREFIX)
    app.include_router(source_test.router, prefix=API_PREFIX)
    app.include_router(search.router, prefix=API_PREFIX)
    app.include_router(entries.router, prefix=API_PREFIX)
    app.include_router(entry_content.router, prefix=API_PREFIX)
    app.include_router(browse.router, prefix=API_PREFIX)
    app.include_router(duplicates.router, prefix=API_PREFIX)
    app.include_router(tags.router, prefix=API_PREFIX)
    app.include_router(analytics.router, prefix=API_PREFIX)
    app.include_router(purge.router, prefix=API_PREFIX)
    app.include_router(webhooks.router, prefix=API_PREFIX)
    app.include_router(scans.router, prefix=API_PREFIX)
    app.include_router(scan_progress.router, prefix=API_PREFIX)
    app.include_router(scan_websocket.router, prefix=WS_PREFIX)
    app.include_router(scan_work.router, prefix=API_PREFIX)
    app.include_router(effective_perms.router, prefix=API_PREFIX)
    app.include_router(identities.router, prefix=API_PREFIX)
    app.include_router(admin_audit.router, prefix=API_PREFIX)
    app.include_router(group_resolution.router, prefix=API_PREFIX)
    app.include_router(principals.router, prefix=API_PREFIX)
    app.include_router(access.router, prefix=API_PREFIX)
    app.include_router(dashboard.router, prefix=API_PREFIX)
    app.include_router(storage_explorer.router, prefix=API_PREFIX)
    app.include_router(scanners.router, prefix=API_PREFIX)
    app.include_router(scanner_protocol.router, prefix=API_PREFIX)
    app.include_router(scanner_discovery.router, prefix=API_PREFIX)
    app.include_router(server_settings.router, prefix=API_PREFIX)
    app.include_router(source_oauth.router, prefix=API_PREFIX)
    app.include_router(health_services.router, prefix=API_PREFIX)
    app.include_router(maintenance.router, prefix=API_PREFIX)
    app.state.akashic_api = True  # lets the test-suite response audit tell this app from throwaway test apps
    return app


app = create_app()
