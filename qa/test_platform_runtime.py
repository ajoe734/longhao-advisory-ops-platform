from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from longhao_platform.app import PlatformRuntime, SyntheticAuthenticator
from longhao_platform.bus import CommandBus, HandlerSpec
from longhao_platform.contracts import EventCatalog, PlatformConfig, ROOT
from longhao_platform.store import InMemoryAtomicStore
from longhao_platform.types import Command, HandlerResult, PendingEvent, PlatformError, Principal, Request


TENANT_A = UUID("3e624789-e990-5c6b-bf39-82de2afce2c8")
TENANT_B = UUID("a2d788c2-2287-5f87-a543-e0ad843ccea7")


def principal(tenant_id: UUID = TENANT_A, roles: frozenset[str] = frozenset({"contract_operator"})) -> Principal:
    return Principal(uuid4(), tenant_id, "operator@synthetic.invalid", roles)


def signed_handler(state, command, _principal):
    next_state = dict(state or {})
    next_state["signed"] = True
    return HandlerResult(
        state=next_state,
        events=(PendingEvent("application.customer_signed", {
            "application_id": str(command.aggregate_id),
            "customer_signature_id": str(uuid4()),
            "package_version_id": str(uuid4()),
            "document_sha256": "e" * 64,
            "customer_signed_at": "2026-11-23T00:00:00Z",
        }),),
        response={"state": "customer_signed"},
        classification="personal",
        changed_fields=("signed",),
    )


@pytest.fixture
def catalog() -> EventCatalog:
    return EventCatalog.load(ROOT / "events/registry.json")


@pytest.fixture
def command() -> Command:
    return Command(
        name="application.record_customer_signature",
        aggregate_type="application",
        aggregate_id=uuid4(),
        expected_version=0,
        idempotency_key="signature:test:0001",
        payload={"document_digest": "a" * 64},
    )


def configured_bus(catalog: EventCatalog) -> tuple[CommandBus, InMemoryAtomicStore]:
    store = InMemoryAtomicStore(catalog)
    bus = CommandBus(store)
    bus.register(
        HandlerSpec(
            command_name="application.record_customer_signature",
            aggregate_type="application",
            producer="M02",
            allowed_roles=frozenset({"contract_operator"}),
            handler=signed_handler,
        )
    )
    return bus, store


def test_command_commit_replay_and_payload_conflict_are_atomic(catalog, command):
    bus, store = configured_bus(catalog)
    actor = principal()

    first = bus.execute(command, actor)
    replay = bus.execute(command, actor)

    assert first.version == 1
    assert replay.replayed is True
    assert replay.event_ids == first.event_ids
    assert len(store.audits()) == 1
    assert len(store.outbox()) == 1
    assert store.aggregate(TENANT_A, "application", command.aggregate_id).state == {"signed": True}

    conflicting = Command(
        name=command.name,
        aggregate_type=command.aggregate_type,
        aggregate_id=command.aggregate_id,
        expected_version=command.expected_version,
        idempotency_key=command.idempotency_key,
        payload={"document_digest": "b" * 64},
    )
    with pytest.raises(PlatformError, match="IDEMPOTENCY_PAYLOAD_CONFLICT") as caught:
        bus.execute(conflicting, actor)
    assert caught.value.status == 409
    assert len(store.audits()) == 1
    assert len(store.outbox()) == 1


def test_stale_version_and_handler_failure_leave_no_partial_rows(catalog, command):
    bus, store = configured_bus(catalog)
    actor = principal()
    bus.execute(command, actor)
    stale = Command(command.name, command.aggregate_type, command.aggregate_id, 0, "signature:test:0002", command.payload)
    with pytest.raises(PlatformError, match="STALE_AGGREGATE_VERSION"):
        bus.execute(stale, actor)
    assert len(store.audits()) == 1
    assert len(store.outbox()) == 1

    failing_store = InMemoryAtomicStore(catalog)
    failing_bus = CommandBus(failing_store)

    def fail_after_calculation(_state, _command, _principal):
        raise PlatformError(409, "INVALID_STATE_TRANSITION")

    failing_bus.register(HandlerSpec(command.name, "application", "M02", frozenset({"contract_operator"}), fail_after_calculation))
    with pytest.raises(PlatformError, match="INVALID_STATE_TRANSITION"):
        failing_bus.execute(command, actor)
    assert failing_store.aggregate(TENANT_A, "application", command.aggregate_id) is None
    assert failing_store.audits() == ()
    assert failing_store.outbox() == ()


def test_invalid_event_schema_prevents_the_entire_commit(catalog, command):
    store = InMemoryAtomicStore(catalog)
    bus = CommandBus(store)

    def invalid_event(_state, _command, _principal):
        return HandlerResult(
            state={"signed": True},
            events=(PendingEvent("application.customer_signed", {"application_id": str(command.aggregate_id)}),),
            classification="personal",
        )

    bus.register(HandlerSpec(command.name, "application", "M02", frozenset({"contract_operator"}), invalid_event))
    with pytest.raises(PlatformError, match="EVENT_SCHEMA_VALIDATION_FAILED"):
        bus.execute(command, principal())
    assert store.aggregate(TENANT_A, "application", command.aggregate_id) is None
    assert store.audits() == ()
    assert store.outbox() == ()


def test_authorization_denial_is_audited_without_business_or_outbox_write(catalog, command):
    bus, store = configured_bus(catalog)
    with pytest.raises(PlatformError, match="AUTHORIZATION_DENIED") as caught:
        bus.execute(command, principal(roles=frozenset({"service_agent"})))
    assert caught.value.status == 403
    assert store.aggregate(TENANT_A, "application", command.aggregate_id) is None
    assert store.outbox() == ()
    assert [row.outcome for row in store.audits()] == ["denied"]


def test_tenant_context_isolation_uses_verified_principal(catalog, command):
    bus, store = configured_bus(catalog)
    first = bus.execute(command, principal(TENANT_A))
    second = bus.execute(command, principal(TENANT_B))
    assert first.version == second.version == 1
    assert store.aggregate(TENANT_A, "application", command.aggregate_id) is not None
    assert store.aggregate(TENANT_B, "application", command.aggregate_id) is not None
    assert len(store.audits(TENANT_A)) == len(store.audits(TENANT_B)) == 1


def test_idempotency_scope_includes_actor_and_event_sequence_is_monotonic(catalog, command):
    bus, store = configured_bus(catalog)
    first_actor = principal()
    second_actor = principal()
    first = bus.execute(command, first_actor)
    second_aggregate = uuid4()
    second = bus.execute(
        Command(command.name, command.aggregate_type, second_aggregate, 0, command.idempotency_key, command.payload),
        second_actor,
    )
    assert first.replayed is second.replayed is False

    update = Command(command.name, command.aggregate_type, command.aggregate_id, 1, "signature:test:0002", command.payload)
    bus.execute(update, first_actor)
    events = [row for row in store.outbox() if row.aggregate_id == command.aggregate_id]
    assert [row.sequence_no for row in events] == [1, 2]
    assert len({row.idempotency_key for row in events}) == 2


def test_binary_float_payload_is_rejected(catalog, command):
    bus, store = configured_bus(catalog)
    floating = Command(
        command.name,
        command.aggregate_type,
        command.aggregate_id,
        0,
        "signature:test:float",
        {"amount": 0.1},
    )
    with pytest.raises(PlatformError, match="NON_CANONICAL_NUMBER"):
        bus.execute(floating, principal())
    assert store.audits() == ()
    assert store.outbox() == ()


def test_runtime_starts_degraded_and_protects_internal_status(catalog):
    config = PlatformConfig(environment="test", synthetic_providers=True, allow_missing_modules=True)
    runtime = PlatformRuntime(config, catalog, SyntheticAuthenticator())
    runtime.module_statuses = []

    status, body = runtime.readiness()
    assert status == 200
    assert body["status"] == "degraded"
    assert body["release_gate_passed"] is False

    with pytest.raises(PlatformError, match="AUTHENTICATION_REQUIRED"):
        runtime.authenticator.authenticate(Request("GET", "/internal/platform/modules", {}))
    authenticated = runtime.authenticator.authenticate(
        Request("GET", "/internal/platform/modules", {"authorization": "Bearer synthetic-platform-admin"})
    )
    assert authenticated.tenant_id == TENANT_A
    with pytest.raises(PlatformError, match="UNTRUSTED_TENANT_HEADER"):
        runtime.authenticator.authenticate(
            Request(
                "GET",
                "/internal/platform/modules",
                {"authorization": "Bearer synthetic-platform-admin", "x-tenant-id": str(TENANT_B)},
            )
        )


def test_manifest_discovery_reports_missing_modules_without_breaking_startup():
    runtime = PlatformRuntime.bootstrap(
        PlatformConfig(environment="test", synthetic_providers=True, allow_missing_modules=True)
    )
    assert len(runtime.module_statuses) == 14
    assert {row.status for row in runtime.module_statuses} == {"missing"}
    assert runtime.readiness()[0] == 200


def test_production_configuration_rejects_fakes_and_missing_modules():
    with pytest.raises(PlatformError, match="SYNTHETIC_PROVIDER_FORBIDDEN"):
        PlatformConfig(environment="production", synthetic_providers=True, allow_missing_modules=False)
    with pytest.raises(PlatformError, match="MISSING_MODULES_FORBIDDEN"):
        PlatformConfig(environment="production", synthetic_providers=False, allow_missing_modules=True)


def test_fastapi_health_problem_details_and_internal_status(catalog):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from longhao_platform.http import create_app

    runtime = PlatformRuntime(
        PlatformConfig(environment="test", synthetic_providers=True, allow_missing_modules=True),
        catalog,
        SyntheticAuthenticator(),
    )
    client = TestClient(create_app(runtime))

    health = client.get("/health/live")
    assert health.status_code == 200
    assert health.json() == {"status": "alive"}
    assert health.headers["X-Correlation-Id"]

    denied = client.get("/internal/platform/modules")
    assert denied.status_code == 401
    assert denied.json()["code"] == "AUTHENTICATION_REQUIRED"
    allowed = client.get(
        "/internal/platform/modules",
        headers={"Authorization": "Bearer synthetic-platform-admin"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["release_gate_passed"] is False
