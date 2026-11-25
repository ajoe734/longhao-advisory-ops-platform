"""``longhao_platform.app.PlatformRuntime.discover_modules()`` registration hook.

Wires this module's own fake/synthetic adapters when the platform is running
with synthetic providers (local/development/test). There is no real
LINE/SMS/email/entitlement/publication adapter yet, so in production
``LegendflowDeliveryService``'s own guard raises and module discovery
correctly reports M09 as broken (``MODULE_REGISTRATION_FAILED``) rather than
silently serving member data through synthetic channel/entitlement ports.
"""
from __future__ import annotations

from typing import Any

from longhao_platform.types import Principal, Request as PlatformRequest

from .domain import ActorContext
from .http import create_fastapi_router
from .ports import (
    FakeChannelProofPort,
    FakeChannelSendPort,
    FakeEntitlementPort,
    FakeIntegrationKeyPort,
    FakePublicationPort,
    FakePublicationRecipientPort,
    FakeReportAccessPort,
)
from .repository import InMemoryRepository
from .service import LegendflowDeliveryService


MODULE_ID = "M09"

#: placeholder key for the synthetic webhook adapter only; never used once a
#: real IntegrationKeyPort/secret store is wired in.
_SYNTHETIC_HMAC_KEY = b"m09-synthetic-platform-hmac-key-32-bytes-min!!"


def register(runtime: Any) -> None:
    repository = InMemoryRepository()
    service = LegendflowDeliveryService(
        repository,
        publications=FakePublicationPort(),
        recipients=FakePublicationRecipientPort(),
        entitlements=FakeEntitlementPort(),
        report_access=FakeReportAccessPort(),
        channel_proof=FakeChannelProofPort(),
        channel_send=FakeChannelSendPort(),
        integration_keys=FakeIntegrationKeyPort(),
        hmac_key=_SYNTHETIC_HMAC_KEY,
        environment=runtime.config.environment,
    )

    def context_resolver(request: Any) -> ActorContext:
        principal: Principal = runtime.authenticator.authenticate(
            PlatformRequest(request.method, request.url.path, dict(request.headers))
        )
        if principal.actor_type == "service":
            principal_type = "service"
        elif principal.actor_type == "customer":
            principal_type = "customer"
        else:
            principal_type = "staff"
        return ActorContext(
            tenant_id=str(principal.tenant_id),
            actor_id=str(principal.actor_id),
            roles=principal.roles,
            principal_type=principal_type,
            customer_id=str(principal.actor_id) if principal_type == "customer" else None,
        )

    router = create_fastapi_router(service, context_resolver)
    runtime.include_router(router)
