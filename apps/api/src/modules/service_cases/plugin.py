"""``longhao_platform.app.PlatformRuntime.discover_modules()`` registration hook.

Wires this module's InMemory repository and its own fake/synthetic adapters
(the same composition used by tests/modules/service_cases). The service's own
environment guard rejects fake ports outside local/development/test, so in
production module discovery reports M13 as broken instead of silently
serving through synthetic adapters.

With synthetic providers the module also joins the development
``SyntheticWorld``: its consumer receives the events it subscribes to, its fake
evidence store learns every registered synthetic asset, and its fakes are
exposed to the world's cross-module projections.
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any

from longhao_platform.synthetic_world import world_for
from longhao_platform.types import PlatformError, Principal, Request as PlatformRequest

from .domain import ActorContext
from .http import create_fastapi_router
from .automation import LocalDraftingAssistant
from .ports import (
    FakeChannelBindings,
    FakeIntegrationKeys,
    FakeKnowledgeBase,
    FakeObjectAssetStore,
    FakeSuppressionRequests,
)
from .repository import InMemoryRepository
from .service import ServiceCaseService

MODULE_ID = "M13"


def _context_resolver(runtime: Any):
    names = {f.name for f in fields(ActorContext)}
    world = world_for(runtime)

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
            # A signed-in member acts as the M01 customer its LegendFlow identity is linked to.
            customer = world.customer_for(str(principal.actor_id)) if world is not None else str(principal.actor_id)
            kwargs["customer_id"] = customer if principal_type == "customer" else None
        if "actor_type" in names:
            kwargs["actor_type"] = principal.actor_type
        try:
            return ActorContext(**kwargs)
        except Exception as exc:  # the module rejects this principal type (e.g. customers on staff-only modules)
            raise PlatformError(403, "PRINCIPAL_NOT_ALLOWED", "This identity type cannot use this module.") from exc

    return resolve


def register(runtime: Any) -> None:
    env = runtime.config.environment
    repository = InMemoryRepository()
    assets = FakeObjectAssetStore()
    knowledge_base = FakeKnowledgeBase()
    channel_bindings = FakeChannelBindings()
    integration_keys = FakeIntegrationKeys()
    suppression = FakeSuppressionRequests()
    service = ServiceCaseService(
        repository, assets, knowledge_base, channel_bindings, suppression,
        LocalDraftingAssistant(), integration_keys, environment=env,
    )
    router = create_fastapi_router(service, _context_resolver(runtime))

    world = world_for(runtime)
    if world is not None:
        from workers.tasks.service_cases.consumer import M13EventConsumer

        world.bind(
            MODULE_ID,
            repository,
            consumer=M13EventConsumer(repository),
            evidence_sink=lambda tenant, asset_id, record: assets.add(tenant, asset_id, record.content),
            service=service,
            knowledge_base=knowledge_base,
            channel_bindings=channel_bindings,
            integration_keys=integration_keys,
        )
        from workers.tasks.service_cases.tasks import LocalMessagingProvider, ResponseDispatchSender, SuppressionJobRunner

        sender = ResponseDispatchSender(repository, LocalMessagingProvider())
        suppression_jobs = SuppressionJobRunner(repository, suppression)
        world.add_worker("M13.response_dispatch", lambda: sender.run_once("dev-world"))
        world.add_worker("M13.suppression_jobs", lambda: suppression_jobs.run_once("dev-world"))
    runtime.include_router(router)
