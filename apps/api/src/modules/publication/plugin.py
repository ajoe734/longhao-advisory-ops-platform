"""``longhao_platform.app.PlatformRuntime.discover_modules()`` registration hook.

Wires this module's InMemory repository and its own fake/synthetic adapters
(the same composition used by tests/modules/publication). The service's own
environment guard rejects fake ports outside local/development/test, so in
production module discovery reports M08 as broken instead of silently
serving through synthetic adapters.
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any

from longhao_platform.types import PlatformError, Principal, Request as PlatformRequest

from .domain import ActorContext
from .http import create_fastapi_router
from .ports import (
    FakeContentAssetStore,
    FakeEntitlementAudienceRegistry,
    FakeEvidenceStore,
    FakeReportBundleRegistry,
)
from .repository import InMemoryRepository
from .service import PublicationService


MODULE_ID = "M08"


def _context_resolver(runtime: Any):
    names = {f.name for f in fields(ActorContext)}

    def resolve(request: Any) -> ActorContext:
        principal: Principal = runtime.authenticator.authenticate(
            PlatformRequest(request.method, request.url.path, dict(request.headers))
        )
        if principal.actor_type == "service":
            principal_type = "service"
        elif principal.actor_type == "customer":
            principal_type = "customer"
        else:
            principal_type = "staff"
        kwargs: dict[str, Any] = {
            "tenant_id": str(principal.tenant_id),
            "actor_id": str(principal.actor_id),
            "roles": principal.roles,
            "principal_type": principal_type,
        }
        if "customer_id" in names:
            kwargs["customer_id"] = str(principal.actor_id) if principal_type == "customer" else None
        if "actor_type" in names:
            kwargs["actor_type"] = principal.actor_type
        try:
            return ActorContext(**kwargs)
        except Exception as exc:  # the module rejects this principal type (e.g. customers on staff-only modules)
            raise PlatformError(403, "PRINCIPAL_NOT_ALLOWED", "This identity type cannot use this module.") from exc

    return resolve


def register(runtime: Any) -> None:
    env = runtime.config.environment
    service = PublicationService(
        InMemoryRepository(),
        FakeContentAssetStore(),
        FakeEvidenceStore(),
        FakeReportBundleRegistry(),
        FakeEntitlementAudienceRegistry(),
        environment=env,
    )
    router = create_fastapi_router(service, _context_resolver(runtime))

    runtime.include_router(router)
