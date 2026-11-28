"""Synthetic development identities: exact markers, fixed tenant, never in production."""
from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from longhao_platform.app import (
    SYNTHETIC_ADMIN_ID,
    SYNTHETIC_TENANT_ID,
    SYNTHETIC_WORKER_ID,
    PlatformRuntime,
    SyntheticAuthenticator,
)
from longhao_platform.contracts import PlatformConfig
from longhao_platform.http import create_app
from longhao_platform.synthetic_identities import PERSONAS, PERSONAS_BY_KEY
from longhao_platform.types import PlatformError, Principal, Request

# Human role codes the module services check (docs/11-security-rbac.md).
HUMAN_ROLES = {
    "cms_operator", "cms_supervisor", "contract_operator", "contract_supervisor", "company_acceptor",
    "kyc_reviewer", "suitability_reviewer", "suitability_authorizer", "aml_officer", "aml_supervisor",
    "analyst", "analyst_reviewer", "analyst_signer", "research_supervisor", "compliance_reviewer",
    "compliance_supervisor", "delivery_operator", "delivery_supervisor", "sales", "sales_supervisor",
    "marketing", "hr_operator", "commission_operator", "commission_approver", "payroll_operator",
    "finance_operator", "finance_checker", "internal_auditor", "service_agent", "service_supervisor",
}


def authenticate(authorization: str, **extra: str) -> Principal:
    return SyntheticAuthenticator().authenticate(Request("GET", "/api/v1/x", {"authorization": authorization, **extra}))


def test_original_markers_are_unchanged():
    admin = authenticate("Bearer synthetic-platform-admin")
    assert (admin.actor_id, admin.subject, admin.roles, admin.actor_type) == (
        SYNTHETIC_ADMIN_ID, "platform-admin@synthetic.invalid", frozenset({"platform_admin", "internal_auditor"}), "human",
    )
    worker = authenticate("Bearer synthetic-worker")
    assert (worker.actor_id, worker.subject, worker.roles, worker.actor_type) == (
        SYNTHETIC_WORKER_ID, "platform-worker@synthetic.invalid", frozenset({"platform_worker"}), "service",
    )


def test_every_persona_is_an_exact_marker_on_the_fixed_tenant():
    ids = set()
    for persona in PERSONAS:
        principal = authenticate(persona.authorization)
        assert principal.tenant_id == SYNTHETIC_TENANT_ID
        assert principal.roles == persona.roles and principal.roles
        assert principal.actor_type in {"human", "customer", "service"}
        ids.add(principal.actor_id)
    assert len(ids) == len(PERSONAS)


@pytest.mark.parametrize("header", [
    "Bearer synthetic-sales ",
    "bearer synthetic-sales",
    "Bearer synthetic-SALES",
    "Bearer synthetic-",
    "Bearer synthetic-sales,analyst",
    "synthetic-sales",
])
def test_near_miss_markers_are_rejected(header):
    with pytest.raises(PlatformError) as caught:
        authenticate(header)
    assert caught.value.code == "AUTHENTICATION_REQUIRED"


def test_tenant_header_is_still_rejected_for_personas():
    with pytest.raises(PlatformError) as caught:
        authenticate("Bearer synthetic-sales", **{"x-tenant-id": str(UUID(int=7))})
    assert caught.value.code == "UNTRUSTED_TENANT_HEADER"


def test_every_module_human_role_has_an_identity():
    covered = set().union(*(p.roles for p in PERSONAS if p.actor_type == "human"))
    assert HUMAN_ROLES - covered == set()


@pytest.mark.parametrize("first,second", [
    ("aml-officer", "aml-officer-2"),
    ("analyst", "analyst-2"),
    ("sales", "sales-2"),
    ("sales-supervisor", "sales-supervisor-2"),
    ("service-agent", "service-agent-2"),
    ("finance-operator", "finance-checker"),
    ("commission-operator", "commission-approver"),
    ("suitability-reviewer", "suitability-authorizer"),
    ("member-a", "member-b"),
])
def test_separation_pairs_are_different_people(first, second):
    assert PERSONAS_BY_KEY[first].id != PERSONAS_BY_KEY[second].id


def test_maker_and_checker_roles_are_not_combined():
    for persona in PERSONAS:
        assert not {"finance_operator", "finance_checker"} <= persona.roles
        assert not {"commission_operator", "commission_approver"} <= persona.roles
        assert not {"aml_officer", "aml_supervisor"} <= persona.roles


def test_dev_identity_listing_is_development_only():
    client = TestClient(create_app(PlatformRuntime.bootstrap()))
    response = client.get("/internal/platform/dev-identities")
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == str(SYNTHETIC_TENANT_ID)
    assert {item["key"] for item in body["items"]} == set(PERSONAS_BY_KEY)

    class RealishAuthenticator:
        def authenticate(self, request: Request) -> Principal:
            raise PlatformError(401, "AUTHENTICATION_REQUIRED")

    other = TestClient(create_app(PlatformRuntime.bootstrap(authenticator=RealishAuthenticator())))
    assert other.get("/internal/platform/dev-identities").status_code == 404


def test_production_never_falls_back_to_synthetic_identities():
    with pytest.raises(PlatformError) as caught:
        PlatformRuntime.bootstrap(
            PlatformConfig(environment="production", synthetic_providers=False, allow_missing_modules=False),
            discover_modules=False,
        )
    assert caught.value.code == "PRODUCTION_AUTHENTICATOR_REQUIRED"


def test_each_module_list_endpoint_is_reachable_by_some_identity():
    app = create_app(PlatformRuntime.bootstrap())
    client = TestClient(app)
    unreachable = []
    for path, item in app.openapi()["paths"].items():
        operation = item.get("get")
        if not operation or "{" in path:
            continue
        if not any(client.get(path, headers={"Authorization": p.authorization}).status_code < 400 for p in PERSONAS):
            unreachable.append(path)
    assert unreachable == []
