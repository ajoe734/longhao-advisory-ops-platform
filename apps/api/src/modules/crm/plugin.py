"""``longhao_platform.app.PlatformRuntime.discover_modules()`` registration hook.

Wires this module's own fake/synthetic evidence adapter when the platform is
running with synthetic providers (local/development/test). There is no real
``core.object_asset`` adapter yet, so in production ``CrmService``'s own
guard raises and module discovery correctly reports M10 as broken
(``MODULE_REGISTRATION_FAILED``) rather than silently approving evidence that
was never actually verified.
"""
from __future__ import annotations

from typing import Any

from longhao_platform.types import Principal, Request as PlatformRequest

from .domain import ActorContext
from .http import create_fastapi_router
from .ports import FakeEvidencePort
from .repository import InMemoryRepository
from .service import CrmService


MODULE_ID = "M10"


def register(runtime: Any) -> None:
    repository = InMemoryRepository()
    service = CrmService(repository, FakeEvidencePort(), environment=runtime.config.environment)

    def context_resolver(request: Any) -> ActorContext:
        principal: Principal = runtime.authenticator.authenticate(
            PlatformRequest(request.method, request.url.path, dict(request.headers))
        )
        return ActorContext(
            tenant_id=str(principal.tenant_id),
            actor_id=str(principal.actor_id),
            roles=principal.roles,
            principal_type="service" if principal.actor_type == "service" else "staff",
        )

    router = create_fastapi_router(service, context_resolver)
    runtime.include_router(router)
