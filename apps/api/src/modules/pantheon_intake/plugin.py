"""``longhao_platform.app.PlatformRuntime.discover_modules()`` registration hook.

Wires this module's InMemory repository and its own fake/synthetic adapters
(the same composition used by tests/modules/pantheon_intake). The service's own
environment guard rejects fake ports outside local/development/test, so in
production module discovery reports M05 as broken instead of silently
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
from longhao_platform.synthetic_identities import SYNTHETIC_TENANT_ID

from .adapter import resolve_pantheon_adapter_context
from .ports import FakeWebhookSignatureVerifier
from .repository import InMemoryRepository
from .service import PantheonIntakeService

MODULE_ID = "M05"
#: synthetic integration key for the Pantheon webhook adapter (development only).
SYNTHETIC_PANTHEON_KEY_ID = "pantheon-integration-01"
SYNTHETIC_PANTHEON_SECRET = b"m05-synthetic-pantheon-webhook-secret-32b!!"


def _pantheon_resolver(runtime: Any, verifier: Any):
    """Candidate ingestion is a signed Pantheon webhook: verify the HMAC at this
    composition boundary, then mint the integration principal (adapter.py).
    Every other route uses the platform authenticator."""
    staff = _context_resolver(runtime)

    def resolve(request: Any) -> Any:
        if request.method != "POST" or not request.url.path.endswith("/research-candidates"):
            return staff(request)

        async def integration() -> ActorContext:
            raw_body = await request.body()
            try:
                return resolve_pantheon_adapter_context(
                    verifier, tenant_id=str(SYNTHETIC_TENANT_ID), method="POST", path=request.url.path,
                    headers=dict(request.headers), raw_body=raw_body,
                )
            except Exception as exc:
                raise PlatformError(401, "INTEGRATION_SIGNATURE_INVALID", "Webhook signature was not accepted.") from exc

        return integration()

    return resolve


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
    signature_verifier = FakeWebhookSignatureVerifier({SYNTHETIC_PANTHEON_KEY_ID: SYNTHETIC_PANTHEON_SECRET})
    service = PantheonIntakeService(repository, environment=env)
    router = create_fastapi_router(service, _pantheon_resolver(runtime, signature_verifier))

    world = world_for(runtime)
    if world is not None:
        from workers.tasks.pantheon_intake.consumer import PantheonIntakeEventConsumer

        world.bind(
            MODULE_ID,
            repository,
            consumer=PantheonIntakeEventConsumer(repository),
            evidence_sink=None,
            service=service,
            signature_verifier=signature_verifier,
        )
    runtime.include_router(router)
