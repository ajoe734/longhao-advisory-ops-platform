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

from longhao_platform.synthetic_world import world_for
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
    publications = FakePublicationPort()
    recipients = FakePublicationRecipientPort()
    entitlements = FakeEntitlementPort()
    report_access = FakeReportAccessPort()
    channel_proof = FakeChannelProofPort()
    channel_send = FakeChannelSendPort()
    integration_keys = FakeIntegrationKeyPort()
    service = LegendflowDeliveryService(
        repository,
        publications=publications,
        recipients=recipients,
        entitlements=entitlements,
        report_access=report_access,
        channel_proof=channel_proof,
        channel_send=channel_send,
        integration_keys=integration_keys,
        hmac_key=_SYNTHETIC_HMAC_KEY,
        environment=runtime.config.environment,
    )

    world = world_for(runtime)

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
            # A signed-in member acts as the M01 customer its LegendFlow identity is linked to.
            customer_id=(world.customer_for(str(principal.actor_id)) if world is not None else str(principal.actor_id))
            if principal_type == "customer" else None,
        )

    router = create_fastapi_router(service, context_resolver)
    if world is not None:
        # Development only: receive subscribed events and expose fakes to cross-module projections.
        from workers.tasks.legendflow_delivery.consumer import M09EventConsumer

        world.bind(
            MODULE_ID,
            repository,
            consumer=M09EventConsumer(repository, service),
            service=service,
            publications=publications,
            recipients=recipients,
            entitlements=entitlements,
            report_access=report_access,
            channel_proof=channel_proof,
            channel_send=channel_send,
            integration_keys=integration_keys,
        )
        from workers.tasks.legendflow_delivery.tasks import DispatchWorker

        dispatcher = DispatchWorker(service)
        world.add_worker("M09.dispatch", lambda: dispatcher.run_once("dev-world", world.tenant_id))
    runtime.include_router(router)
