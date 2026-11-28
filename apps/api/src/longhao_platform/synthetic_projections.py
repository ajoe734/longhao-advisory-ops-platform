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


# ------------------------------------------------- research -> publication
def project_validated_candidate_to_m07(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """M07 only drafts reports for an existing review case and a known candidate lineage."""
    payload = envelope["payload"]
    if payload.get("validation_result") not in (None, "validated", "passed", "accepted"):
        return
    review_cases = world.service("M07", "review_cases")
    candidates = world.service("M07", "candidates")
    if review_cases is not None and payload.get("review_case_id"):
        review_cases.add(world.tenant_id, payload["review_case_id"])
    if candidates is not None:
        candidates.add(world.tenant_id, payload["candidate_id"])


def _active_entitlements(world: SyntheticWorld) -> list[Any]:
    return _read(world, "M02", lambda state: [
        row for (tenant, _), row in state.entitlements.items() if tenant == world.tenant_id and row.status == "active"
    ]) or []


def project_signed_report_to_m08(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """Before M08 publishes: the signed bundle it verifies and the audience it resolves.

    The bundle is registered for every M08 recommendation version that cites
    the signed report version (M08's own matching rule). Audience candidates
    are the currently active M02 entitlements.
    """
    payload = envelope["payload"]
    bundles = world.service("M08", "report_bundles")
    audiences = world.service("M08", "audiences")
    if bundles is None or audiences is None:
        return
    from modules.publication.ports import AudienceCandidate

    report_version_id = payload["report_version_id"]
    versions, series = _read(world, "M08", lambda state: (
        [v for (t, _), v in state.versions.items() if t == world.tenant_id and report_version_id in v.supporting_report_version_ids],
        {k[1]: s for k, s in state.series.items() if k[0] == world.tenant_id},
    ))
    candidates = tuple(
        AudienceCandidate(customer_id=row.customer_id, entitlement_id=row.id, contract_id=row.contract_id,
                          entitlement_version=row.version, eligible=True, reason_code="active_entitlement")
        for row in _active_entitlements(world)
    )
    for version in versions:
        bundles.register(world.tenant_id, report_version_id, version.id,
                         content_sha256=version.content_sha256, report_content_sha256=payload["report_content_sha256"])
        # M08's fake derives its own manifest digest; the signed manifest is the one M07 actually
        # signed (carried by report.signature_verified), so that is what the bundle must present.
        bundles._manifests[(world.tenant_id, report_version_id)] = {
            "report_content_sha256": payload["report_content_sha256"],
            "bundle_manifest_sha256": payload["bundle_manifest_sha256"],
        }
        instrument_ref = series[version.series_id].instrument_ref
        audiences.set_candidates(world.tenant_id, instrument_ref, candidates)


def _entitlement_status(world: SyntheticWorld, entitlement_id: str) -> Any:
    from modules.legendflow_delivery.ports import EntitlementStatus

    row = _read(world, "M02", lambda state: state.entitlements.get((world.tenant_id, entitlement_id)))
    if row is None:
        return None
    return EntitlementStatus(entitlement_id=row.id, customer_id=row.customer_id, state=row.status,
                             decision_id=row.last_decision_id or row.id, valid_until=row.valid_until)


def project_publication_to_m09(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """Before M09 dispatches: the published snapshot, its recipients and their entitlements."""
    payload = envelope["payload"]
    publications = world.service("M09", "publications")
    if publications is None:
        return
    from modules.legendflow_delivery.ports import PublicationSnapshot, RecipientSnapshot

    publication_id = payload["publication_id"]
    publication, version, recipients = _read(world, "M08", lambda state: (
        state.publications.get((world.tenant_id, publication_id)),
        None,
        [r for (t, _), r in state.recipients.items() if t == world.tenant_id and r.publication_id == publication_id],
    ))
    if publication is None:
        return
    version = _read(world, "M08", lambda state: state.versions.get((world.tenant_id, publication.recommendation_version_id)))
    report = _read(world, "M07", lambda state: state.versions.get((world.tenant_id, publication.report_version_id)))
    texts = list(getattr(report, "channel_texts", None) or [])
    text = next((row for row in texts if row.get("channel") == "legendflow"), texts[0] if texts else {})
    publications.register(world.tenant_id, PublicationSnapshot(
        publication_id=publication.id,
        report_version_id=publication.report_version_id,
        signature_id=publication.signature_id,
        recommendation_series_id=version.series_id,
        recommendation_version_id=version.id,
        action=version.action,
        approved_text=text.get("body", ""),
        body_sha256=text.get("body_sha256", ""),
        report_version_ids=tuple(version.supporting_report_version_ids),
        published_at=publication.published_at or publication.created_at,
        valid_until=publication.valid_until,
    ))
    world.service("M09", "recipients").set(world.tenant_id, publication_id, [
        RecipientSnapshot(recipient_id=r.id, customer_id=r.customer_id, entitlement_id=r.entitlement_id,
                          entitlement_version=r.entitlement_version, eligibility_reason=r.eligibility_reason)
        for r in recipients
    ])
    for r in recipients:
        status = _entitlement_status(world, r.entitlement_id)
        if status is not None:
            world.service("M09", "entitlements").set(world.tenant_id, status)
    if report is not None:
        world.service("M09", "report_access").register(
            world.tenant_id, publication.report_version_id, report.bundle_manifest.get("report_content_sha256", ""))


def project_entitlement_to_m09(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    entitlements = world.service("M09", "entitlements")
    status = _entitlement_status(world, envelope["payload"]["entitlement_id"])
    if entitlements is not None and status is not None:
        entitlements.set(world.tenant_id, status)


# ------------------------------------------------------------- commissions
def _current_owner(world: SyntheticWorld, customer_id: str) -> str | None:
    def reader(state: Any) -> str | None:
        assignment_id = state.current_assignment.get((world.tenant_id, customer_id))
        assignment = state.assignments.get((world.tenant_id, assignment_id)) if assignment_id else None
        return None if assignment is None or assignment.valid_until is not None else assignment.owner_actor_id
    return _read(world, "M11", reader)


def _customer_year(world: SyntheticWorld, customer_id: str) -> int:
    """Years since the customer's first effective contract (M02), starting at 1."""
    def reader(state: Any) -> list[datetime]:
        return [row.effective_at for (tenant, _), row in state.contracts.items()
                if tenant == world.tenant_id and row.customer_id == customer_id and getattr(row, "effective_at", None)]
    starts = _read(world, "M02", reader) or []
    if not starts:
        return 1
    from datetime import timezone
    first = min(starts)
    return max(1, (datetime.now(timezone.utc) - first).days // 365 + 1)


def _set_cohort(world: SyntheticWorld, customer_id: str) -> None:
    ownership = world.service("M12", "ownership")
    if ownership is None or not customer_id:
        return
    from modules.commissions.ports import CohortFact

    customer = _read(world, "M01", lambda state: state.customers.get((world.tenant_id, customer_id)))
    ownership.set(world.tenant_id, CohortFact(
        customer_id=customer_id,
        customer_year=_customer_year(world, customer_id),
        # a first-year customer has not renewed yet; renewal history is not modelled in the dev world
        continuous_auto_renew=False,
        current_owner_id=_current_owner(world, customer_id),
        marketing_attribution_id=getattr(customer, "original_marketing_attribution_id", None),
    ))


def project_recognition_to_m12(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    """M12 reads finance-confirmed revenue, the customer cohort and any approved sales conversion."""
    revenue = world.service("M12", "revenue")
    conversions = world.service("M12", "conversions")
    payload = envelope["payload"]
    if revenue is None or conversions is None:
        return
    recognition = _read(world, "M14", lambda state: state.revenue_recognitions.get((world.tenant_id, payload["recognition_id"])))
    contract = _read(world, "M14", lambda state: state.contracts.get((world.tenant_id, payload["contract_id"])))
    if recognition is None or contract is None or not contract.customer_id or not recognition.commission_eligible:
        return
    from modules.commissions.ports import ConversionFact, RecognitionFact

    revenue.add_recognition(world.tenant_id, RecognitionFact(
        recognition.id, payload["contract_id"], contract.customer_id, recognition.recognized_at,
        recognition.untaxed_amount, recognition.currency,
    ))
    _set_cohort(world, contract.customer_id)
    conversion = _read(world, "M10", lambda state: next(
        (row for (tenant, _), row in state.conversions.items()
         if tenant == world.tenant_id and row.contract_service_id == payload.get("contract_service_id")
         and row.decision_status == "approved" and row.sales_actor_id), None))
    if conversion is not None:
        conversions.set(world.tenant_id, recognition.id, ConversionFact(conversion.id, conversion.sales_actor_id, True))


def project_assignment_to_m12(world: SyntheticWorld, envelope: dict[str, Any]) -> None:
    _set_cohort(world, envelope["payload"].get("customer_id"))


EARLY_PROJECTIONS: dict[str, tuple[Callable[[SyntheticWorld, dict[str, Any]], None], ...]] = {
    "report.signature_verified": (project_signed_report_to_m08,),
    "publication.created": (project_publication_to_m09,),
    "finance.revenue_recognized": (project_recognition_to_m12,),
    "ownership.assignment_changed": (project_assignment_to_m12,),
}


PROJECTIONS: dict[str, tuple[Callable[[SyntheticWorld, dict[str, Any]], None], ...]] = {
    "research.candidate_validated": (project_validated_candidate_to_m07,),
    "entitlement.changed": (project_entitlement_to_m09,),
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
    for event_type, projections in EARLY_PROJECTIONS.items():
        for projection in projections:
            world.on(event_type, projection, before_consumers=True)
    for event_type, projections in PROJECTIONS.items():
        for projection in projections:
            world.on(event_type, projection)
