"""``longhao_platform.app.PlatformRuntime.discover_modules()`` registration hook.

Wires this module's InMemory repository and its own fake/synthetic adapters
(the same composition used by tests/modules/commissions). The service's own
environment guard rejects fake ports outside local/development/test, so in
production module discovery reports M12 as broken instead of silently
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
from .ports import FakeEvidenceStore, FakeOwnershipCohort, FakeRevenueBasis, FakeSalesConversion
from .repository import InMemoryRepository
from .service import CommissionService

MODULE_ID = "M12"


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
    revenue = FakeRevenueBasis()
    conversions = FakeSalesConversion()
    ownership = FakeOwnershipCohort()
    evidence = FakeEvidenceStore()
    service = CommissionService(repository, revenue, conversions, ownership, evidence, environment=env)
    router = create_fastapi_router(service, _context_resolver(runtime))

    world = world_for(runtime)
    if world is not None:
        from workers.tasks.commissions.consumer import CommissionEventConsumer

        world.bind(
            MODULE_ID,
            repository,
            consumer=CommissionEventConsumer(repository, revenue),
            evidence_sink=lambda tenant, asset_id, record: evidence.add(tenant, asset_id),
            service=service,
            revenue=revenue,
            conversions=conversions,
            ownership=ownership,
        )
        from workers.tasks.commissions.tasks import CommissionJobRunner

        jobs = CommissionJobRunner(repository, service)
        world.add_worker("M12.commission_jobs", lambda: jobs.run_once("dev-world"))
    runtime.include_router(router)
