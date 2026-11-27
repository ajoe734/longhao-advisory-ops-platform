"""FastAPI HTTP boundary for health, shared operations and module routers."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Request as FastAPIRequest
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from longhao_platform.app import PlatformRuntime, SyntheticAuthenticator
from longhao_platform.synthetic_world import existing_world
from longhao_platform.types import PlatformError, Principal, Request


def create_app(runtime: PlatformRuntime | None = None) -> FastAPI:
    runtime = runtime or PlatformRuntime.bootstrap()
    app = FastAPI(
        title="Longhao Advisory Operations Platform",
        version="0.1.0",
        docs_url=None if runtime.config.environment == "production" else "/docs",
        redoc_url=None,
    )
    app.state.runtime = runtime

    @app.middleware("http")
    async def correlation_middleware(request: FastAPIRequest, call_next):
        correlation_id = str(uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        world = existing_world(runtime)
        if world is not None and request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Development only: deliver the events this request produced to subscribed modules.
            world.tick()
        response.headers["X-Correlation-Id"] = correlation_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PlatformError)
    async def platform_error(request: FastAPIRequest, exc: PlatformError):
        correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
        return JSONResponse(
            status_code=exc.status,
            content={
                "type": f"https://example.invalid/problems/{exc.code.lower()}",
                "title": exc.code,
                "status": exc.status,
                "code": exc.code,
                "detail": exc.detail,
                "correlation_id": correlation_id,
                "retryable": exc.retryable,
            },
            media_type="application/problem+json",
        )

    def current_principal(request: FastAPIRequest) -> Principal:
        headers = {key.lower(): value for key, value in request.headers.items()}
        return runtime.authenticator.authenticate(Request(request.method, request.url.path, headers))

    def operations_reader(principal: Principal = Depends(current_principal)) -> Principal:
        runtime.require_roles(principal, frozenset({"platform_admin", "internal_auditor", "platform_worker"}))
        return principal

    @app.get("/health/live", include_in_schema=False)
    async def live():
        return {"status": "alive"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready():
        status, body = runtime.readiness(disclose_modules=runtime.config.environment != "production")
        return JSONResponse(status_code=status, content=body)

    if runtime.config.environment != "production" and isinstance(runtime.authenticator, SyntheticAuthenticator):
        # Local/development only: the fixed synthetic markers the admin shell can act as.
        @app.get("/internal/platform/dev-identities", include_in_schema=False)
        async def dev_identities():
            return {"tenant_id": str(runtime.authenticator.tenant_id()), "items": runtime.authenticator.personas()}

    if existing_world(runtime) is not None:
        @app.get("/internal/platform/world", include_in_schema=False)
        async def world_status(_principal: Principal = Depends(operations_reader)):
            return existing_world(runtime).status()

        @app.post("/internal/platform/world/assets", include_in_schema=False)
        async def register_synthetic_asset(request: FastAPIRequest, principal: Principal = Depends(current_principal)):
            """Development only: stand-in for an upload to the object store, so a signed-in
            operator can attach synthetic evidence to an action from the admin shell."""
            body = await request.json()
            label = str(body.get("label") or "admin shell evidence")[:160]
            classification = str(body.get("classification") or "internal")
            if classification not in {"internal", "customer_confidential", "aml_restricted", "research_confidential"}:
                raise PlatformError(422, "ASSET_CLASSIFICATION_INVALID")
            record = existing_world(runtime).register_asset(
                f"{label} by {principal.subject} {uuid4()}", classification=classification,
                customer_id=body.get("customer_id"), content=body.get("content"),
            )
            return JSONResponse(status_code=201, content=record.public_view())

    @app.get("/internal/platform/modules", include_in_schema=False)
    async def modules(_principal: Principal = Depends(operations_reader)):
        status, body = runtime.readiness(disclose_modules=True)
        return JSONResponse(status_code=status, content=body)

    @app.get("/internal/platform/outbox", include_in_schema=False)
    async def outbox(principal: Principal = Depends(operations_reader)):
        return {
            "items": [
                {
                    "event_id": str(row.event_id),
                    "event_type": row.event_type,
                    "producer": row.producer,
                    "status": row.status,
                    "attempt_count": row.attempt_count,
                    "last_error_code": row.last_error_code,
                }
                for row in runtime.store.outbox(principal.tenant_id)
            ]
        }

    for router in runtime.routers:
        app.include_router(router, prefix="/api/v1")

    admin_dist = runtime.config.admin_root
    if admin_dist.is_dir() and (admin_dist / "index.html").is_file():
        assets = admin_dist / "assets"
        if assets.is_dir():
            app.mount("/admin/assets", StaticFiles(directory=assets), name="admin-assets")

        @app.get("/admin/{path:path}", include_in_schema=False)
        async def admin_shell(path: str):
            candidate = admin_dist / path
            if path and candidate.is_file() and candidate.resolve().is_relative_to(admin_dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(admin_dist / "index.html")

    return app
