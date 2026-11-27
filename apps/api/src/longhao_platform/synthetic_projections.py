"""Development-only projections between module fakes, driven by published events.

A module's fake lookup port (for example M02's application source) stands in
for a read of another module's data. In production that read is a real
adapter over the owning module's store; here each projection copies the
owning module's current record into the downstream fake when the owning
module publishes the corresponding event. Projections never write into the
owning module and never decide anything: they only make already-recorded
facts visible to the next module.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from longhao_platform.synthetic_world import SyntheticWorld


def _parse(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read(world: SyntheticWorld, module_id: str, reader: Callable[[Any], Any]) -> Any:
    binding = world.modules.get(module_id)
    return None if binding is None else binding.repository.read(reader)


# ---------------------------------------------------------------- applications
def _m01_application(world: SyntheticWorld, application_id: str) -> Any:
    return _read(world, "M01", lambda state: state.applications.get((world.tenant_id, application_id)))


def project_application_to_m02(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """M02 reads the application (selections, version, state) it is signing/accepting."""
    application_id = envelope["payload"].get("application_id")
    applications = world.service("M02", "applications")
    if not application_id or applications is None:
        return
    application = _m01_application(world, application_id)
    if application is None:
        return
    from modules.contracts_entitlements.ports import ApplicationSnapshot

    applications.register(ApplicationSnapshot(
        id=application.id,
        tenant_id=application.tenant_id,
        version=application.version,
        customer_id=application.customer_id,
        package_version_id=application.package_version_id,
        service_selections=tuple(dict(row) for row in application.service_selections),
        intent=application.intent,
        preceding_contract_id=application.preceding_contract_id,
        state=application.state,
    ))


def project_application_to_m04(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """M04 checks which customer an application belongs to before screening it."""
    payload = envelope["payload"]
    applications = world.service("M04", "applications")
    if applications is not None and payload.get("application_id") and payload.get("customer_id"):
        applications.register(world.tenant_id, payload["application_id"], payload["customer_id"])


def project_customer_to_m03(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    customers = world.service("M03", "customers")
    customer_id = envelope["payload"].get("customer_id")
    if customers is None or not customer_id:
        return
    customer = _read(world, "M01", lambda state: state.customers.get((world.tenant_id, customer_id)))
    customers.register(world.tenant_id, customer_id, getattr(customer, "version", 1))


def project_signed_application_to_m03(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """M03 starts KYC only for an application the customer has signed."""
    payload = envelope["payload"]
    applications = world.service("M03", "applications")
    application = _m01_application(world, payload["application_id"])
    if applications is None or application is None:
        return
    applications.register(
        world.tenant_id,
        payload["application_id"],
        application.customer_id,
        payload["customer_signature_id"],
        _parse(payload["customer_signed_at"]),
        version=application.version,
    )


def project_consent_as_evidence(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """Downstream modules cite a recorded M01 consent by id as evidence (e-sign, AML/KYC declarations)."""
    payload = envelope["payload"]
    if payload.get("granted") and payload.get("consent_id"):
        world.register_asset(
            f"consent {payload['purpose']} {payload['consent_id']}",
            asset_id=payload["consent_id"],
            customer_id=payload.get("customer_id"),
            classification="customer_confidential",
        )


# -------------------------------------------------------- M02 acceptance gates
def _version_of(world: SyntheticWorld, module_id: str, collection: str, record_id: str) -> int:
    record = _read(world, module_id, lambda state: getattr(state, collection).get((world.tenant_id, record_id)))
    return int(getattr(record, "version", 1) or 1)


def project_kyc_gate(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """M02 re-reads the current KYC decision for the application before acceptance."""
    payload = envelope["payload"]
    gates = world.service("M02", "gates")
    if gates is None or not payload.get("application_id"):
        return
    from modules.contracts_entitlements.ports import KycStatus

    gates.set_kyc(world.tenant_id, payload["application_id"], KycStatus(
        submission_id=payload["submission_id"],
        decision=payload["decision"],
        version=_version_of(world, "M03", "submissions", payload["submission_id"]),
    ))


def project_suitability_gate(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    payload = envelope["payload"]
    gates = world.service("M02", "gates")
    if gates is None:
        return
    from modules.contracts_entitlements.ports import SuitabilityStatus

    gates.set_suitability(world.tenant_id, payload["application_id"], SuitabilityStatus(
        assessment_id=payload["assessment_id"],
        state=payload["state"],
        version=_version_of(world, "M03", "assessments", payload["assessment_id"]),
    ))


def project_clearance_gate(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    payload = envelope["payload"]
    gates = world.service("M02", "gates")
    if gates is None:
        return
    from modules.contracts_entitlements.ports import ClearanceStatus

    gates.set_clearance(world.tenant_id, payload["application_id"], ClearanceStatus(
        clearance_id=payload["clearance_id"],
        decision=payload["decision"],
        version=_version_of(world, "M04", "clearances", payload["clearance_id"]),
        expires_at=_parse(payload.get("expires_at")),
    ))


def project_payment_condition(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """M02 activates entitlements only once the application's payment condition is met."""
    payload = envelope["payload"]
    payments = world.service("M02", "payments")
    if payments is not None and payload.get("application_id"):
        payments.set_condition(world.tenant_id, payload["application_id"], payload["payment_id"])


PROJECTIONS: dict[str, tuple[Callable[[SyntheticWorld, dict[str, Any]], None], ...]] = {
    "finance.payment_confirmed": (project_payment_condition,),
    "kyc.decision_recorded": (project_kyc_gate,),
    "suitability.assessed": (project_suitability_gate,),
    "business_clearance.changed": (project_clearance_gate,),
    "customer.consent_changed": (project_consent_as_evidence,),
    "application.created": (project_application_to_m02, project_application_to_m04),
    "application.customer_signed": (project_application_to_m02, project_signed_application_to_m03),
    "application.company_decided": (project_application_to_m02,),
    "customer.created": (project_customer_to_m03,),
    "customer.profile_updated": (project_customer_to_m03,),
}


def install(world: SyntheticWorld) -> None:
    for event_type, projections in PROJECTIONS.items():
        for projection in projections:
            world.on(event_type, projection)
