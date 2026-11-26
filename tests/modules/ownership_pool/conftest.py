from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

from modules.ownership_pool.domain import ActorContext, canonical_hash, wire_time
from modules.ownership_pool.ports import FakeEvidencePort, FakeRestrictionPort
from modules.ownership_pool.repository import InMemoryRepository
from modules.ownership_pool.service import OwnershipPoolService
from workers.tasks.ownership_pool.consumer import M11EventConsumer


TENANT = "10000000-0000-0000-0000-000000000001"
OTHER_TENANT = "10000000-0000-0000-0000-000000000002"
SUPERVISOR = "20000000-0000-0000-0000-000000000001"
SUPERVISOR_B = "20000000-0000-0000-0000-000000000006"
SALES_A = "20000000-0000-0000-0000-000000000002"
SALES_B = "20000000-0000-0000-0000-000000000003"
HR_OPERATOR = "20000000-0000-0000-0000-000000000004"
SERVICE_ACTOR = "20000000-0000-0000-0000-000000000005"
HR_EVIDENCE = "30000000-0000-0000-0000-000000000001"
HANDOFF_EVIDENCE = "30000000-0000-0000-0000-000000000002"
NOW = datetime(2026, 11, 23, 1, 2, 3, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def m11():
    repository = InMemoryRepository()
    evidence = FakeEvidencePort()
    evidence.register(TENANT, HR_EVIDENCE)
    evidence.register(TENANT, HANDOFF_EVIDENCE)
    restriction = FakeRestrictionPort()
    clock = FixedClock()
    service = OwnershipPoolService(repository, evidence, restriction, clock=clock)
    consumer = M11EventConsumer(repository, restriction, clock=clock)
    return {
        "service": service,
        "repository": repository,
        "evidence": evidence,
        "restriction": restriction,
        "clock": clock,
        "consumer": consumer,
    }


@pytest.fixture
def supervisor() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR, frozenset({"sales_supervisor"}), principal_type="staff")


@pytest.fixture
def supervisor_b() -> ActorContext:
    return ActorContext(TENANT, SUPERVISOR_B, frozenset({"sales_supervisor"}), principal_type="staff")


@pytest.fixture
def sales_a() -> ActorContext:
    return ActorContext(TENANT, SALES_A, frozenset({"sales"}), principal_type="staff")


@pytest.fixture
def sales_b() -> ActorContext:
    return ActorContext(TENANT, SALES_B, frozenset({"sales"}), principal_type="staff")


@pytest.fixture
def hr_operator() -> ActorContext:
    return ActorContext(TENANT, HR_OPERATOR, frozenset({"hr_operator"}), principal_type="staff")


@pytest.fixture
def ownership_service() -> ActorContext:
    return ActorContext(TENANT, SERVICE_ACTOR, frozenset({"ownership_service"}), principal_type="service")


@pytest.fixture
def other_tenant_sales() -> ActorContext:
    return ActorContext(OTHER_TENANT, "20000000-0000-0000-0000-000000000007", frozenset({"sales"}), principal_type="staff")


@pytest.fixture
def other_tenant_supervisor() -> ActorContext:
    return ActorContext(OTHER_TENANT, "20000000-0000-0000-0000-000000000008", frozenset({"sales_supervisor"}), principal_type="staff")


def seed_policy(service: OwnershipPoolService, supervisor_ctx: ActorContext, supervisor_b_ctx: ActorContext | None = None, *, lapse_days: int = 7, approve: bool = True) -> str:
    """Propose (and usually approve) the default pool policy.

    Approval requires a different actor than the proposer (SoD, matching the
    shared migration's ``approved_by<>proposed_by`` CHECK) — a plain
    ``seed_policy(service, supervisor, supervisor)`` call therefore always
    approves as the well-known second supervisor, not the proposer itself.
    """
    proposed = service.propose_policy_version(supervisor_ctx, lapse_days=lapse_days)
    policy_id = proposed.data["id"]
    if approve:
        approver = supervisor_b_ctx if supervisor_b_ctx and supervisor_b_ctx.actor_id != supervisor_ctx.actor_id else ActorContext(TENANT, SUPERVISOR_B, frozenset({"sales_supervisor"}), principal_type="staff")
        service.approve_policy_version(approver, policy_id, expected_version=1)
    return proposed.data["policy_version"]


def _envelope(*, event_type, producer, aggregate_type, aggregate_id, aggregate_version, sequence_no, payload, occurred_at=NOW):
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "schema_version": "1.0.0",
        "tenant_id": TENANT,
        "producer": producer,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": aggregate_version,
        "sequence_no": sequence_no,
        "occurred_at": wire_time(occurred_at),
        "emitted_at": wire_time(occurred_at),
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "idempotency_key": f"{event_type}:{aggregate_id}:{sequence_no}",
        "classification": "internal" if event_type == "ownership.employee_departed" else "customer_confidential",
        "payload_sha256": canonical_hash(payload),
        "payload": payload,
    }


def contract_effective_envelope(customer_id: str, contract_id: str, *, starts_at: datetime, ends_at: datetime, sequence_no: int = 1) -> dict:
    return _envelope(
        event_type="contract.effective",
        producer="M02",
        aggregate_type="contract",
        aggregate_id=contract_id,
        aggregate_version=1,
        sequence_no=sequence_no,
        payload={
            "contract_id": contract_id,
            "customer_id": customer_id,
            "effective_at": wire_time(starts_at),
            "service_starts_at": wire_time(starts_at),
            "service_ends_at": wire_time(ends_at),
        },
        occurred_at=starts_at,
    )


def contract_terminated_envelope(customer_id: str, contract_id: str, *, terminated_at: datetime, sequence_no: int = 2) -> dict:
    return _envelope(
        event_type="contract.terminated",
        producer="M02",
        aggregate_type="contract",
        aggregate_id=contract_id,
        aggregate_version=2,
        sequence_no=sequence_no,
        payload={
            "contract_id": contract_id,
            "customer_id": customer_id,
            "terminated_at": wire_time(terminated_at),
            "refund_evaluation_required": False,
        },
        occurred_at=terminated_at,
    )


def contract_service_ended_envelope(
    customer_id: str, contract_id: str, contract_service_id: str, *, ended_at: datetime, sequence_no: int = 1,
) -> dict:
    return _envelope(
        event_type="contract.service_ended",
        producer="M02",
        aggregate_type="contract_service",
        aggregate_id=contract_service_id,
        aggregate_version=1,
        sequence_no=sequence_no,
        payload={
            "contract_service_id": contract_service_id,
            "contract_id": contract_id,
            "customer_id": customer_id,
            "ended_at": wire_time(ended_at),
        },
        occurred_at=ended_at,
    )


def seed_customer_with_contract(consumer: M11EventConsumer, customer_id: str, contract_id: str, *, starts_at: datetime, ends_at: datetime) -> None:
    result = consumer.consume(contract_effective_envelope(customer_id, contract_id, starts_at=starts_at, ends_at=ends_at))
    assert result.status == "applied"


def current_version(m11, customer_id: str) -> int:
    snapshot = m11["repository"].snapshot()
    assignment_id = snapshot.current_assignment[(TENANT, customer_id)]
    return snapshot.assignments[(TENANT, assignment_id)].version
