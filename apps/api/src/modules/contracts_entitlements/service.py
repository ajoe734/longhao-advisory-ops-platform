from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

from .domain import (
    AcceptanceDecision,
    AmendmentRecord,
    AuditEvent,
    CompanyAcceptance,
    ConflictError,
    Contract,
    ContractService,
    CustomerSignature,
    DomainError,
    Entitlement,
    EntitlementDecision,
    ForbiddenError,
    IdempotencyReceipt,
    Job,
    NotFoundError,
    OutboxEvent,
    PackageVersion,
    PreconditionRequiredError,
    SignatureSession,
    StaleVersionError,
    ValidationError,
    canonical_hash,
    new_id,
    require_sha256,
    require_uuid,
    utc_now,
    wire_time,
)
from .ports import (
    ApplicationSourcePort,
    ClearancePort,
    EvidencePort,
    KycPort,
    PaymentPort,
    SignatureProviderPort,
    SuitabilityPort,
)
from .repository import InMemoryRepository, StoreState


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_PENDING_STATES = {"pending_company_acceptance"}
_AMENDMENT_TYPES = {"upgrade", "renewal", "scope_change"}
_RETURN_ROUTES = {"application_progress", "member_contracts"}
_MEDIA_TYPES = {"application/pdf", "application/json", "text/plain", "image/jpeg", "image/png"}
_DOC_CLASSIFICATIONS = {"internal", "customer_confidential", "aml_restricted", "research_confidential"}


@dataclass(frozen=True)
class ServiceResponse:
    data: dict[str, Any]
    status: int = 200
    etag: str | None = None
    idempotency_replayed: bool = False


@dataclass
class _Outcome:
    value: dict[str, Any]
    status: int
    aggregate_type: str
    aggregate_id: str | None
    aggregate_version: int | None
    error: DomainError | None = None


class ContractsEntitlementsService:
    """M02 command/query application service.

    Every state-changing operation runs through one repository callback so that
    domain state, audit, outbox and the idempotency receipt commit atomically.
    Gate state (KYC/suitability/AML/payment) is never trusted from a cached
    event projection; it is re-read from its owning module's port at decision
    time, matching the "consumer不能以收到事件當已獲授權" rule in the SA doc.
    """

    def __init__(
        self,
        repository: InMemoryRepository,
        applications: ApplicationSourcePort,
        kyc: KycPort,
        suitability: SuitabilityPort,
        clearance: ClearancePort,
        payments: PaymentPort,
        signature_provider: SignatureProviderPort,
        evidence: EvidencePort,
        *,
        clock: Callable[[], datetime] = utc_now,
        environment: str = "test",
    ) -> None:
        if environment == "production" and any(
            getattr(adapter, "is_fake", False)
            for adapter in (repository, applications, kyc, suitability, clearance, payments, signature_provider, evidence)
        ):
            raise RuntimeError("fake M02 adapters are forbidden in production")
        self.repository = repository
        self.applications = applications
        self.kyc = kyc
        self.suitability = suitability
        self.clearance = clearance
        self.payments = payments
        self.signature_provider = signature_provider
        self.evidence = evidence
        self._clock = clock

    # ---- contract packages ------------------------------------------------

    def create_contract_package(self, context, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._authorize_staff(context, {"contract_operator"})
        self._body(body, {"package_code", "version_label", "documents", "service_codes", "effective_from", "acceptance_terms_version"},
                   {"package_code", "version_label", "documents", "service_codes", "effective_from", "acceptance_terms_version"})
        if not isinstance(body["package_code"], str) or not 1 <= len(body["package_code"]) <= 80:
            raise ValidationError("package_code must contain 1 to 80 characters", field_name="package_code")
        if not isinstance(body["version_label"], str) or not 1 <= len(body["version_label"]) <= 40:
            raise ValidationError("version_label must contain 1 to 40 characters", field_name="version_label")
        documents = self._validate_documents(body["documents"])
        service_codes = body["service_codes"]
        if not isinstance(service_codes, list) or not 1 <= len(service_codes) <= 20 or any(not isinstance(v, str) or len(v) > 80 for v in service_codes):
            raise ValidationError("service_codes must contain 1 to 20 short strings", field_name="service_codes")
        effective_from = self._parse_time(body["effective_from"], "effective_from")
        terms_version = str(body["acceptance_terms_version"])
        if not 1 <= len(terms_version) <= 80:
            raise ValidationError("acceptance_terms_version must contain 1 to 80 characters", field_name="acceptance_terms_version")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            sequence_key = (context.tenant_id, body["package_code"])
            sequence_no = state.package_sequence.get(sequence_key, 0) + 1
            state.package_sequence[sequence_key] = sequence_no
            package = PackageVersion(
                id=new_id(), tenant_id=context.tenant_id, package_code=body["package_code"],
                version_label=body["version_label"], sequence_no=sequence_no, documents=documents,
                service_codes=service_codes, effective_from=effective_from, acceptance_terms_version=terms_version,
                proposed_by=context.actor_id, package_sha256=canonical_hash(documents), created_at=now, updated_at=now,
            )
            state.packages[(context.tenant_id, package.id)] = package
            return self._package_outcome(package, context, 201)

        return self._run_command(context, "create_contract_package", idempotency_key, body, correlation_id, mutate)

    def approve_contract_package(
        self, context, package_version_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None,
    ) -> ServiceResponse:
        package_version_id = require_uuid(package_version_id, "package_version_id")
        self._authorize_staff(context, {"compliance_reviewer"})
        self._body(body, {"decision", "reason_code", "note", "evidence_asset_ids"}, {"decision", "reason_code"})
        if body["decision"] not in {"approve", "reject", "request_changes"}:
            raise ValidationError("decision is not supported", field_name="decision")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            package = self._stored_package(state, context.tenant_id, package_version_id)
            self._cas(package.version, expected_version)
            if body["decision"] != "approve":
                return self._package_outcome(package, context, 200)
            if package.state != "draft":
                raise ConflictError("only a draft package can be approved", code="package_not_draft")
            if package.proposed_by == context.actor_id:
                raise ForbiddenError("the proposer cannot approve their own package", code="segregation_conflict")
            package.state = "approved"
            package.approved_by = context.actor_id
            package.approved_at = now
            package.version += 1
            package.updated_at = now
            return self._package_outcome(package, context, 200)

        return self._run_command(context, "approve_contract_package", idempotency_key, {"package_version_id": package_version_id, **body}, correlation_id, mutate)

    def get_contract_package(self, context, package_version_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        package_version_id = require_uuid(package_version_id, "package_version_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            package = self._stored_package(state, context.tenant_id, package_version_id)
            if context.principal_type == "customer" and package.state != "approved":
                raise NotFoundError("package was not found")
            if context.principal_type == "staff" and not context.roles & {"contract_operator", "compliance_reviewer"}:
                raise ForbiddenError("actor cannot read contract packages")
            self._audit(state, context, "get_contract_package", "contract_package", package.id, package.version, correlation_id, "succeeded")
            return ServiceResponse(package.to_api(self._package_actions(package, context)), etag=self._etag(package.version))

        return self.repository.atomic(query)

    # ---- customer signature ------------------------------------------------

    def create_customer_signature(
        self, context, application_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None,
    ) -> ServiceResponse:
        application_id = require_uuid(application_id, "application_id")
        self._body(body, {"package_version_id", "package_sha256", "electronic_signing_consent_id", "return_route"},
                   {"package_version_id", "package_sha256", "electronic_signing_consent_id", "return_route"})
        package_version_id = require_uuid(body["package_version_id"], "package_version_id")
        package_sha256 = require_sha256(body["package_sha256"], "package_sha256")
        consent_id = require_uuid(body["electronic_signing_consent_id"], "electronic_signing_consent_id")
        if body["return_route"] not in _RETURN_ROUTES:
            raise ValidationError("return_route is not supported", field_name="return_route")
        application = self.applications.get(context.tenant_id, application_id)
        self._authorize_customer_resource(context, application.customer_id, {"contract_operator"})
        self._cas(application.version, expected_version)
        if application.state != "draft":
            raise ConflictError("application is not awaiting a customer signature", code="application_not_signable")
        package = self._find_package(context.tenant_id, package_version_id)
        if package is None or package.tenant_id != context.tenant_id:
            raise NotFoundError("contract package was not found")
        if package.state != "approved":
            raise ValidationError("only an approved package may be signed", code="package_not_approved")
        if package.package_sha256 != package_sha256:
            raise ValidationError("package_sha256 does not match the approved package", code="package_hash_mismatch", field_name="package_sha256")
        if not self.evidence.exists(context.tenant_id, consent_id):
            raise ValidationError("electronic signing consent evidence does not exist", code="evidence_not_found", field_name="electronic_signing_consent_id")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()
        draft = self.signature_provider.create_session(context.tenant_id, application_id, package_version_id)

        def mutate(state: StoreState) -> _Outcome:
            session = SignatureSession(
                id=new_id(), tenant_id=context.tenant_id, application_id=application_id, package_version_id=package_version_id,
                customer_id=application.customer_id, package_sha256=package_sha256, electronic_signing_consent_id=consent_id,
                return_route=body["return_route"], provider_session_ref=draft.provider_session_ref, redirect_url=draft.redirect_url,
                expires_at=draft.expires_at, created_at=now, updated_at=now,
            )
            state.sessions[(context.tenant_id, session.id)] = session
            state.session_by_ref[draft.provider_session_ref] = (context.tenant_id, session.id)
            return _Outcome(session.to_api([]), 202, "signature_session", session.id, session.version)

        return self._run_command(context, "create_customer_signature", idempotency_key, {"application_id": application_id, **body}, correlation_id, mutate)

    def receive_customer_signature(self, context, body: dict[str, Any], *, idempotency_key: str, correlation_id: str | None = None) -> ServiceResponse:
        self._body(
            body,
            {"event_id", "provider_session_ref", "application_id", "package_version_id", "document_sha256",
             "signed_asset_id", "signed_file_sha256", "provider_signed_at", "verification_evidence_asset_id", "result"},
            {"event_id", "provider_session_ref", "application_id", "package_version_id", "document_sha256", "result"},
        )
        event_id = require_uuid(body["event_id"], "event_id")
        application_id = require_uuid(body["application_id"], "application_id")
        package_version_id = require_uuid(body["package_version_id"], "package_version_id")
        document_sha256 = require_sha256(body["document_sha256"], "document_sha256")
        if body["result"] not in {"signed", "failed", "expired"}:
            raise ValidationError("result is not supported", field_name="result")
        if body["result"] == "signed":
            for field_name in ("signed_asset_id", "signed_file_sha256", "provider_signed_at", "verification_evidence_asset_id"):
                if field_name not in body:
                    raise ValidationError(f"{field_name} is required when result=signed", field_name=field_name)
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            session_key = state.session_by_ref.get(body["provider_session_ref"])
            if session_key is None:
                raise NotFoundError("signature session was not found")
            session = state.sessions[session_key]
            if session.application_id != application_id or session.package_version_id != package_version_id:
                raise ValidationError("callback does not match the originating session", code="signature_session_mismatch")
            if session.status != "pending":
                return _Outcome(self._receipt(event_id, "duplicate", correlation_id), 202, "signature_session", session.id, session.version)
            if body["result"] != "signed":
                session.status = "failed" if body["result"] == "failed" else "expired"
                session.version += 1
                session.updated_at = now
                return _Outcome(self._receipt(event_id, "accepted", correlation_id), 202, "signature_session", session.id, session.version)
            package = state.packages.get((context.tenant_id, package_version_id))
            if package is None or package.state == "retired" or package.package_sha256 != document_sha256:
                session.status = "failed"
                session.version += 1
                session.updated_at = now
                raise ValidationError("signed document does not match the current approved package", code="package_hash_mismatch")
            signed_file_sha256 = require_sha256(body["signed_file_sha256"], "signed_file_sha256")
            signed_asset_id = require_uuid(body["signed_asset_id"], "signed_asset_id")
            verification_evidence_asset_id = require_uuid(body["verification_evidence_asset_id"], "verification_evidence_asset_id")
            provider_signed_at = self._parse_time(body["provider_signed_at"], "provider_signed_at")
            provider_tx_key = ("legendflow_esign", event_id)
            existing_tx = state.provider_tx_index.get(provider_tx_key)
            if existing_tx is not None and existing_tx != session.id:
                raise ConflictError("provider transaction id was already used by another signature", code="duplicate_provider_transaction")
            signature = CustomerSignature(
                id=new_id(), tenant_id=context.tenant_id, application_id=application_id, package_version_id=package_version_id,
                customer_id=session.customer_id, session_id=session.id, content_sha256=document_sha256,
                signed_file_sha256=signed_file_sha256, signed_asset_id=signed_asset_id, provider="legendflow_esign",
                provider_transaction_id=event_id, assurance_level="aal2", customer_signed_at=provider_signed_at,
                evidence_asset_id=verification_evidence_asset_id, created_at=now,
            )
            state.signatures[(context.tenant_id, signature.id)] = signature
            state.signatures_by_application[(context.tenant_id, application_id)] = signature.id
            state.provider_tx_index[provider_tx_key] = session.id
            session.status = "verified"
            session.version += 1
            session.updated_at = now
            self._emit(
                state, event_type="application.customer_signed", tenant_id=context.tenant_id, aggregate_type="application",
                aggregate_id=application_id, aggregate_version=1, correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:customer-signed",
                payload={
                    "application_id": application_id, "customer_signature_id": signature.id,
                    "package_version_id": package_version_id, "document_sha256": document_sha256,
                    "customer_signed_at": wire_time(provider_signed_at),
                },
                now=now,
            )
            return _Outcome(self._receipt(event_id, "accepted", correlation_id), 202, "signature_session", session.id, session.version)

        return self._run_command(context, "receive_customer_signature", idempotency_key, body, correlation_id, mutate)

    # ---- company acceptance -------------------------------------------------

    def decide_company_acceptance(
        self, context, application_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None,
    ) -> ServiceResponse:
        application_id = require_uuid(application_id, "application_id")
        self._authorize_staff(context, {"company_acceptor"})
        allowed = {"decision", "kyc_submission_id", "suitability_assessment_id", "business_clearance_id", "package_version_id",
                   "customer_signature_id", "intended_effective_at", "reason_code", "company_signature_evidence_asset_id", "payment_condition_version"}
        self._body(body, allowed, {"decision", "package_version_id", "customer_signature_id", "reason_code"})
        if body["decision"] not in {"accept", "reject", "request_changes"}:
            raise ValidationError("decision is not supported", field_name="decision")
        package_version_id = require_uuid(body["package_version_id"], "package_version_id")
        customer_signature_id = require_uuid(body["customer_signature_id"], "customer_signature_id")
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        if body["decision"] == "accept":
            for field_name in ("kyc_submission_id", "suitability_assessment_id", "business_clearance_id", "intended_effective_at", "payment_condition_version"):
                if field_name not in body:
                    raise ValidationError(f"{field_name} is required when decision=accept", field_name=field_name)
        application = self.applications.get(context.tenant_id, application_id)
        self._cas(application.version, expected_version)
        package = self._find_package(context.tenant_id, package_version_id)
        if package is None:
            raise NotFoundError("contract package was not found")
        if context.actor_id in {package.proposed_by, package.approved_by}:
            raise ForbiddenError("the package drafter or approver cannot also decide company acceptance", code="segregation_conflict")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            signature = state.signatures.get((context.tenant_id, customer_signature_id))
            if signature is None or signature.application_id != application_id:
                raise NotFoundError("customer signature was not found")

            if body["decision"] != "accept":
                decision = AcceptanceDecision(
                    id=new_id(), tenant_id=context.tenant_id, application_id=application_id, customer_signature_id=customer_signature_id,
                    decision=body["decision"], decided_by=context.actor_id, decided_at=now, safe_reason_code=reason_code,
                    evidence_snapshot={"evidence_asset_ids": [customer_signature_id]}, created_at=now,
                )
                state.acceptance_decisions[(context.tenant_id, decision.id)] = decision
                if body["decision"] == "reject" and self.payments.condition_ref(context.tenant_id, application_id):
                    job = Job(id=new_id(), tenant_id=context.tenant_id, kind="refund_review",
                              payload={"application_id": application_id, "reason_code": reason_code}, created_at=now, updated_at=now)
                    state.jobs[(context.tenant_id, job.id)] = job
                self._emit(
                    state, event_type="application.company_decided", tenant_id=context.tenant_id, aggregate_type="application",
                    aggregate_id=application_id, aggregate_version=1, correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:company-decided",
                    payload={"application_id": application_id, "acceptance_id": decision.id, "decision": body["decision"], "contract_id": None},
                    now=now,
                )
                return _Outcome(decision.to_api([]), 200, "acceptance_decision", decision.id, 1)

            intended_effective_at = self._parse_time(body["intended_effective_at"], "intended_effective_at")
            kyc = self.kyc.current(context.tenant_id, application_id)
            if kyc is None or kyc.decision != "approved":
                raise ValidationError("KYC has not been approved", code="gate_not_satisfied")
            if kyc.submission_id != str(body["kyc_submission_id"]):
                raise StaleVersionError("a newer KYC submission supersedes this reference", code="stale_assessment")
            suitability = self.suitability.current(context.tenant_id, application_id)
            if suitability is None or suitability.state not in {"suitable", "conditional"}:
                raise ValidationError("suitability assessment is not satisfied", code="gate_not_satisfied")
            if suitability.assessment_id != str(body["suitability_assessment_id"]):
                raise StaleVersionError("a newer suitability assessment supersedes this reference", code="stale_assessment")
            clearance = self.clearance.current(context.tenant_id, application_id)
            if clearance is None or clearance.decision != "approved" or (clearance.expires_at and clearance.expires_at <= now):
                raise ValidationError("business clearance is not satisfied", code="gate_not_satisfied")
            if clearance.clearance_id != str(body["business_clearance_id"]):
                raise StaleVersionError("a newer business clearance supersedes this reference", code="stale_assessment")

            decision = AcceptanceDecision(
                id=new_id(), tenant_id=context.tenant_id, application_id=application_id, customer_signature_id=customer_signature_id,
                decision="accept", decided_by=context.actor_id, decided_at=now, safe_reason_code=reason_code,
                evidence_snapshot={"evidence_asset_ids": [customer_signature_id], "intended_effective_at": wire_time(intended_effective_at)},
                created_at=now,
            )
            state.acceptance_decisions[(context.tenant_id, decision.id)] = decision
            acceptance = CompanyAcceptance(
                id=new_id(), tenant_id=context.tenant_id, decision_id=decision.id, application_id=application_id,
                customer_signature_id=customer_signature_id, kyc_submission_id=kyc.submission_id,
                suitability_assessment_id=suitability.assessment_id, aml_clearance_id=clearance.clearance_id,
                accepted_by=context.actor_id, accepted_at=now,
                conditions_snapshot={"kyc_version": kyc.version, "suitability_version": suitability.version, "clearance_version": clearance.version},
                intended_effective_at=intended_effective_at, payment_condition_version=str(body["payment_condition_version"]),
                created_at=now, updated_at=now,
            )
            state.company_acceptances[(context.tenant_id, acceptance.id)] = acceptance
            service_windows: list[dict[str, Any]] = []
            contract = Contract(
                id=new_id(), tenant_id=context.tenant_id, application_id=application_id, customer_id=application.customer_id,
                customer_signature_id=customer_signature_id, company_acceptance_id=acceptance.id, package_version_id=package_version_id,
                contract_number=f"LHA-{now.year}-{new_id()[:8].upper()}", currency=self._currency(application.service_selections),
                terms_snapshot={"service_windows": service_windows}, intended_effective_at=intended_effective_at,
                preceding_contract_id=application.preceding_contract_id, service_selections=list(application.service_selections),
                created_at=now, updated_at=now,
            )
            chain_id = new_id()
            for selection in application.service_selections:
                starts_at = intended_effective_at
                ends_at = self._billing_period_end(starts_at, selection["billing_interval"])
                contract_service = ContractService(
                    id=new_id(), tenant_id=context.tenant_id, contract_id=contract.id, service_code=selection["service_code"],
                    service_chain_id=chain_id, starts_at=starts_at, ends_at=ends_at,
                    untaxed_contract_amount=selection["quoted_price"]["amount"], tax_profile_ref=selection["tax_mapping_version"],
                    features_snapshot=selection, auto_renew=False, created_at=now, updated_at=now,
                )
                state.contract_services[(context.tenant_id, contract_service.id)] = contract_service
                entitlement = Entitlement(
                    id=new_id(), tenant_id=context.tenant_id, customer_id=application.customer_id, contract_id=contract.id,
                    contract_service_id=contract_service.id, feature_code=selection["service_code"], valid_from=starts_at,
                    valid_until=ends_at, status="locked", created_at=now, updated_at=now,
                )
                state.entitlements[(context.tenant_id, entitlement.id)] = entitlement
                state.entitlement_by_service_feature[(context.tenant_id, contract_service.id, entitlement.feature_code)] = entitlement.id
                service_windows.append({"starts_at": wire_time(starts_at), "ends_at": wire_time(ends_at)})
            decision.contract_id = contract.id
            state.contracts[(context.tenant_id, contract.id)] = contract
            state.contract_by_application[(context.tenant_id, application_id)] = contract.id
            self._emit(
                state, event_type="application.company_decided", tenant_id=context.tenant_id, aggregate_type="application",
                aggregate_id=application_id, aggregate_version=1, correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:company-decided",
                payload={"application_id": application_id, "acceptance_id": decision.id, "decision": "accept", "contract_id": contract.id},
                now=now,
            )
            return _Outcome(decision.to_api([]), 200, "acceptance_decision", decision.id, 1)

        return self._run_command(context, "decide_company_acceptance", idempotency_key, {"application_id": application_id, **body}, correlation_id, mutate)

    # ---- contracts and amendments ------------------------------------------

    def get_contract(self, context, contract_id: str, *, correlation_id: str | None = None) -> ServiceResponse:
        contract_id = require_uuid(contract_id, "contract_id")
        correlation_id = self._correlation(correlation_id)

        def query(state: StoreState) -> ServiceResponse:
            contract = self._stored_contract(state, context.tenant_id, contract_id)
            self._authorize_customer_resource(context, contract.customer_id, {"contract_operator"})
            signature = state.signatures[(context.tenant_id, contract.customer_signature_id)]
            acceptance = state.company_acceptances[(context.tenant_id, contract.company_acceptance_id)]
            self._audit(state, context, "get_contract", "contract", contract.id, contract.version, correlation_id, "succeeded")
            return ServiceResponse(
                contract.to_api(self._contract_actions(contract, context), customer_signed_at=signature.customer_signed_at, company_accepted_at=acceptance.accepted_at),
                etag=self._etag(contract.version),
            )

        return self.repository.atomic(query)

    def create_contract_amendment(
        self, context, contract_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None,
    ) -> ServiceResponse:
        contract_id = require_uuid(contract_id, "contract_id")
        self._body(body, {"amendment_type", "new_package_version_id", "service_selections", "effective_at", "opportunity_id"},
                   {"amendment_type", "new_package_version_id", "service_selections", "effective_at"})
        if body["amendment_type"] not in _AMENDMENT_TYPES:
            raise ValidationError("amendment_type is not supported", field_name="amendment_type")
        new_package_version_id = require_uuid(body["new_package_version_id"], "new_package_version_id")
        self._validate_service_selections(body["service_selections"])
        effective_at = self._parse_time(body["effective_at"], "effective_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            contract = self._stored_contract(state, context.tenant_id, contract_id)
            self._authorize_customer_resource(context, contract.customer_id, {"contract_operator"})
            self._cas(contract.version, expected_version)
            if contract.status not in {"accepted", "effective"}:
                raise ConflictError("contract is not open for amendment", code="contract_not_amendable")
            selection = body["service_selections"][0]
            predecessor = next(
                (row for (tenant, _), row in state.contract_services.items()
                 if tenant == context.tenant_id and row.contract_id == contract_id and row.service_code == selection["service_code"]),
                None,
            )
            if predecessor is None:
                raise NotFoundError("no existing contract service matches this amendment")
            if selection["billing_interval"] not in {"month", "year"}:
                raise ValidationError("amendments require a recurring billing_interval", field_name="service_selections.0.billing_interval")
            starts_at = max(effective_at, predecessor.ends_at)
            ends_at = self._billing_period_end(starts_at, selection["billing_interval"])
            new_service = ContractService(
                id=new_id(), tenant_id=context.tenant_id, contract_id=contract_id, service_code=selection["service_code"],
                service_chain_id=predecessor.service_chain_id, predecessor_service_id=predecessor.id, starts_at=starts_at,
                ends_at=ends_at, untaxed_contract_amount=selection["quoted_price"]["amount"], tax_profile_ref=selection["tax_mapping_version"],
                features_snapshot=selection, auto_renew=False, created_at=now, updated_at=now,
            )
            state.contract_services[(context.tenant_id, new_service.id)] = new_service
            entitlement = Entitlement(
                id=new_id(), tenant_id=context.tenant_id, customer_id=contract.customer_id, contract_id=contract_id,
                contract_service_id=new_service.id, feature_code=new_service.service_code, valid_from=starts_at,
                valid_until=ends_at, status="locked", created_at=now, updated_at=now,
            )
            state.entitlements[(context.tenant_id, entitlement.id)] = entitlement
            state.entitlement_by_service_feature[(context.tenant_id, new_service.id, entitlement.feature_code)] = entitlement.id
            record = AmendmentRecord(
                id=new_id(), tenant_id=context.tenant_id, customer_id=contract.customer_id, package_version_id=new_package_version_id,
                contract_id=contract_id, new_contract_service_id=new_service.id, created_at=now, updated_at=now,
            )
            state.amendments[(context.tenant_id, record.id)] = record
            return _Outcome(record.to_api(), 201, "contract_service", new_service.id, new_service.version)

        return self._run_command(context, "create_contract_amendment", idempotency_key, {"contract_id": contract_id, **body}, correlation_id, mutate)

    def terminate_contract(
        self, context, contract_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None,
    ) -> ServiceResponse:
        contract_id = require_uuid(contract_id, "contract_id")
        self._authorize_staff(context, {"contract_operator"})
        self._body(body, {"requested_at", "effective_at", "reason_code", "customer_request_evidence_id", "refund_evaluation_required"},
                   {"requested_at", "effective_at", "reason_code", "customer_request_evidence_id", "refund_evaluation_required"})
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        evidence_id = require_uuid(body["customer_request_evidence_id"], "customer_request_evidence_id")
        if not self.evidence.exists(context.tenant_id, evidence_id):
            raise ValidationError("termination evidence does not exist", code="evidence_not_found", field_name="customer_request_evidence_id")
        if not isinstance(body["refund_evaluation_required"], bool):
            raise ValidationError("refund_evaluation_required must be boolean", field_name="refund_evaluation_required")
        effective_at = self._parse_time(body["effective_at"], "effective_at")
        self._parse_time(body["requested_at"], "requested_at")
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            contract = self._stored_contract(state, context.tenant_id, contract_id)
            self._cas(contract.version, expected_version)
            if contract.status == "terminated":
                signature = state.signatures[(context.tenant_id, contract.customer_signature_id)]
                acceptance = state.company_acceptances[(context.tenant_id, contract.company_acceptance_id)]
                return _Outcome(
                    contract.to_api(self._contract_actions(contract, context), customer_signed_at=signature.customer_signed_at, company_accepted_at=acceptance.accepted_at),
                    200, "contract", contract.id, contract.version,
                )
            contract.status = "terminated"
            contract.terminated_at = effective_at
            contract.obligations_ended_at = effective_at
            contract.version += 1
            contract.updated_at = now
            for (tenant, _), service_row in state.contract_services.items():
                if tenant != context.tenant_id or service_row.contract_id != contract_id:
                    continue
                for (etenant, _), entitlement in state.entitlements.items():
                    if etenant != context.tenant_id or entitlement.contract_service_id != service_row.id:
                        continue
                    if entitlement.status == "revoked":
                        continue
                    entitlement.status = "revoked"
                    entitlement.version += 1
                    entitlement.updated_at = now
                    self._emit(
                        state, event_type="entitlement.changed", tenant_id=context.tenant_id, aggregate_type="entitlement",
                        aggregate_id=entitlement.id, aggregate_version=entitlement.version, correlation_id=correlation_id,
                        idempotency_key=f"{idempotency_key}:entitlement-{entitlement.id}",
                        payload={"entitlement_id": entitlement.id, "customer_id": entitlement.customer_id, "contract_id": contract_id,
                                 "state": "revoked", "decision_id": new_id(), "valid_until": wire_time(entitlement.valid_until)},
                        now=now,
                    )
                self._emit(
                    state, event_type="contract.service_ended", tenant_id=context.tenant_id, aggregate_type="contract_service",
                    aggregate_id=service_row.id, aggregate_version=1, correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:service-ended-{service_row.id}",
                    payload={"contract_service_id": service_row.id, "contract_id": contract_id, "customer_id": contract.customer_id, "ended_at": wire_time(effective_at)},
                    now=now,
                )
            self._emit(
                state, event_type="contract.terminated", tenant_id=context.tenant_id, aggregate_type="contract",
                aggregate_id=contract_id, aggregate_version=contract.version, correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:contract-terminated",
                payload={"contract_id": contract_id, "customer_id": contract.customer_id, "terminated_at": wire_time(effective_at),
                         "refund_evaluation_required": body["refund_evaluation_required"]},
                now=now,
            )
            if body["refund_evaluation_required"]:
                job = Job(id=new_id(), tenant_id=context.tenant_id, kind="refund_review",
                          payload={"contract_id": contract_id, "reason_code": reason_code}, created_at=now, updated_at=now)
                state.jobs[(context.tenant_id, job.id)] = job
            signature = state.signatures[(context.tenant_id, contract.customer_signature_id)]
            acceptance = state.company_acceptances[(context.tenant_id, contract.company_acceptance_id)]
            return _Outcome(
                contract.to_api(self._contract_actions(contract, context), customer_signed_at=signature.customer_signed_at, company_accepted_at=acceptance.accepted_at),
                200, "contract", contract.id, contract.version,
            )

        return self._run_command(context, "terminate_contract", idempotency_key, {"contract_id": contract_id, **body}, correlation_id, mutate)

    # ---- entitlements -------------------------------------------------------

    def list_customer_entitlements(
        self, context, customer_id: str, *, state_filter: str | None = None, cursor: str | None = None, limit: int = 50,
        as_of: datetime | None = None, correlation_id: str | None = None,
    ) -> ServiceResponse:
        customer_id = require_uuid(customer_id, "customer_id")
        self._authorize_customer_resource(context, customer_id, {"contract_operator"})
        if not 1 <= limit <= 200:
            raise ValidationError("limit must be between 1 and 200", field_name="limit")
        if state_filter and state_filter not in {"locked", "eligible", "active", "suspended", "expired", "revoked"}:
            raise ValidationError("state filter is not supported", field_name="state")
        correlation_id = self._correlation(correlation_id)
        snapshot_at = as_of or self._clock()

        def query(state: StoreState) -> ServiceResponse:
            rows = sorted(
                (
                    row for (tenant, _), row in state.entitlements.items()
                    if tenant == context.tenant_id and row.customer_id == customer_id and row.created_at <= snapshot_at
                    and (not state_filter or row.status == state_filter) and (not cursor or row.id > cursor)
                ),
                key=lambda row: row.id,
            )
            page = rows[:limit]
            self._audit(state, context, "list_customer_entitlements", "entitlement", None, None, correlation_id, "succeeded")
            return ServiceResponse({
                "items": [row.to_api(self._entitlement_actions(row, context)) for row in page],
                "next_cursor": page[-1].id if len(rows) > limit else None,
                "as_of": wire_time(snapshot_at),
                "total_estimate": len(rows),
            })

        return self.repository.atomic(query)

    def evaluate_entitlements(
        self, context, contract_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None,
    ) -> ServiceResponse:
        contract_id = require_uuid(contract_id, "contract_id")
        if context.principal_type != "service" or "entitlement_service" not in context.roles:
            raise ForbiddenError("only the entitlement service identity may evaluate entitlements")
        self._body(body, {"cause_event_id", "policy_version", "evaluation_at"}, {"cause_event_id", "policy_version"})
        cause_event_id = require_uuid(body["cause_event_id"], "cause_event_id")
        policy_version = str(body["policy_version"])
        if not 1 <= len(policy_version) <= 80:
            raise ValidationError("policy_version must contain 1 to 80 characters", field_name="policy_version")
        explicit_now = self._parse_time(body["evaluation_at"], "evaluation_at") if body.get("evaluation_at") else None
        correlation_id = self._correlation(correlation_id)
        now = explicit_now or self._clock()

        def mutate(state: StoreState) -> _Outcome:
            contract = self._stored_contract(state, context.tenant_id, contract_id)
            self._cas(contract.version, expected_version)
            payment_ref = self.payments.condition_ref(context.tenant_id, contract.application_id)
            services = [row for (tenant, _), row in state.contract_services.items() if tenant == context.tenant_id and row.contract_id == contract_id]
            touched_ids: list[str] = []
            snapshots: list[dict[str, Any]] = []
            priority = {"grant": 5, "revoke": 4, "suspend": 3, "expire": 2, "deny": 1, "keep": 0}
            overall = "keep"
            went_active = False
            for service_row in services:
                entitlement_id = state.entitlement_by_service_feature.get((context.tenant_id, service_row.id, service_row.service_code))
                entitlement = state.entitlements[(context.tenant_id, entitlement_id)]
                target = self._compute_target(entitlement, service_row, now, contract.status, payment_ref)
                word = self._transition_word(entitlement.status, target, payment_ref)
                if priority[word] > priority[overall]:
                    overall = word
                if target != entitlement.status:
                    entitlement.status = target
                    entitlement.version += 1
                    entitlement.updated_at = now
                    if target == "active":
                        entitlement.payment_condition_ref = payment_ref
                        went_active = True
                    touched_ids.append(entitlement.id)
                    self._emit(
                        state, event_type="entitlement.changed", tenant_id=context.tenant_id, aggregate_type="entitlement",
                        aggregate_id=entitlement.id, aggregate_version=entitlement.version, correlation_id=correlation_id,
                        idempotency_key=f"{idempotency_key}:entitlement-{entitlement.id}",
                        payload={"entitlement_id": entitlement.id, "customer_id": entitlement.customer_id, "contract_id": contract_id,
                                 "state": target, "decision_id": cause_event_id, "valid_until": wire_time(entitlement.valid_until)},
                        now=now,
                    )
                    if target == "expired":
                        self._emit(
                            state, event_type="contract.service_ended", tenant_id=context.tenant_id, aggregate_type="contract_service",
                            aggregate_id=service_row.id, aggregate_version=1, correlation_id=correlation_id,
                            idempotency_key=f"{idempotency_key}:service-ended-{service_row.id}",
                            payload={"contract_service_id": service_row.id, "contract_id": contract_id, "customer_id": contract.customer_id, "ended_at": wire_time(now)},
                            now=now,
                        )
                snapshots.append({
                    "gate": "service_period", "result": "pass" if target == "active" else "pending" if target in {"eligible", "locked"} else "fail",
                    "record_id": service_row.id, "record_version": service_row.version, "evaluated_at": wire_time(now),
                })
            if went_active and contract.status == "accepted":
                contract.status = "effective"
                contract.effective_at = now
                contract.version += 1
                contract.updated_at = now
                self._emit(
                    state, event_type="contract.effective", tenant_id=context.tenant_id, aggregate_type="contract",
                    aggregate_id=contract_id, aggregate_version=contract.version, correlation_id=correlation_id,
                    idempotency_key=f"{idempotency_key}:contract-effective",
                    payload={"contract_id": contract_id, "customer_id": contract.customer_id, "effective_at": wire_time(now),
                             "service_starts_at": wire_time(min(row.starts_at for row in services)) if services else wire_time(now),
                             "service_ends_at": wire_time(max(row.ends_at for row in services)) if services else wire_time(now)},
                    now=now,
                )
            all_entitlement_ids = [state.entitlement_by_service_feature[(context.tenant_id, row.id, row.service_code)] for row in services]
            decision = EntitlementDecision(
                id=new_id(), tenant_id=context.tenant_id, contract_id=contract_id, decision=overall, policy_version=policy_version,
                condition_snapshots=snapshots or [{"gate": "contract_effective", "result": "pending", "record_id": contract_id, "record_version": contract.version, "evaluated_at": wire_time(now)}],
                entitlement_ids=all_entitlement_ids, decided_at=now, created_at=now,
            )
            state.entitlement_decisions[(context.tenant_id, decision.id)] = decision
            for eid in all_entitlement_ids:
                state.entitlements[(context.tenant_id, eid)].last_decision_id = decision.id
            return _Outcome(decision.to_api([]), 202, "entitlement_decision", decision.id, 1)

        return self._run_command(context, "evaluate_entitlements", idempotency_key, {"contract_id": contract_id, **body}, correlation_id, mutate)

    def suspend_entitlement(
        self, context, entitlement_id: str, body: dict[str, Any], *, expected_version: int | None, idempotency_key: str, correlation_id: str | None = None,
    ) -> ServiceResponse:
        entitlement_id = require_uuid(entitlement_id, "entitlement_id")
        self._authorize_staff(context, {"contract_supervisor"})
        self._body(body, {"reason_code", "evidence_ref", "until", "customer_notice_template_version"}, {"reason_code", "evidence_ref"})
        reason_code = str(body["reason_code"])
        if not 1 <= len(reason_code) <= 80:
            raise ValidationError("reason_code must contain 1 to 80 characters", field_name="reason_code")
        evidence_ref = require_uuid(body["evidence_ref"], "evidence_ref")
        if not self.evidence.exists(context.tenant_id, evidence_ref):
            raise ValidationError("suspension evidence does not exist", code="evidence_not_found", field_name="evidence_ref")
        until = self._parse_time(body["until"], "until") if body.get("until") else None
        correlation_id = self._correlation(correlation_id)
        now = self._clock()

        def mutate(state: StoreState) -> _Outcome:
            entitlement = self._stored_entitlement(state, context.tenant_id, entitlement_id)
            self._cas(entitlement.version, expected_version)
            if entitlement.status == "suspended":
                return _Outcome(entitlement.to_api(self._entitlement_actions(entitlement, context)), 200, "entitlement", entitlement.id, entitlement.version)
            if entitlement.status in {"expired", "revoked"}:
                raise ConflictError("a terminal entitlement cannot be suspended", code="entitlement_terminal")
            entitlement.status = "suspended"
            entitlement.restriction_codes = sorted(set(entitlement.restriction_codes) | {reason_code})
            entitlement.version += 1
            entitlement.updated_at = now
            decision = EntitlementDecision(
                id=new_id(), tenant_id=context.tenant_id, contract_id=entitlement.contract_id, decision="suspend",
                policy_version="manual_supervisor_action", condition_snapshots=[{
                    "gate": "service_period", "result": "fail", "record_id": evidence_ref, "record_version": 1, "evaluated_at": wire_time(now),
                }],
                entitlement_ids=[entitlement.id], decided_at=now, created_at=now,
            )
            state.entitlement_decisions[(context.tenant_id, decision.id)] = decision
            entitlement.last_decision_id = decision.id
            self._emit(
                state, event_type="entitlement.changed", tenant_id=context.tenant_id, aggregate_type="entitlement",
                aggregate_id=entitlement.id, aggregate_version=entitlement.version, correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:entitlement-{entitlement.id}",
                payload={"entitlement_id": entitlement.id, "customer_id": entitlement.customer_id, "contract_id": entitlement.contract_id,
                         "state": "suspended", "decision_id": decision.id, "valid_until": wire_time(until or entitlement.valid_until)},
                now=now,
            )
            return _Outcome(entitlement.to_api(self._entitlement_actions(entitlement, context)), 200, "entitlement", entitlement.id, entitlement.version)

        return self._run_command(context, "suspend_entitlement", idempotency_key, {"entitlement_id": entitlement_id, **body}, correlation_id, mutate)

    # ---- command transaction helpers ---------------------------------------

    def _run_command(self, context, operation: str, key: str, request: dict[str, Any], correlation_id: str, mutate: Callable[[StoreState], _Outcome]) -> ServiceResponse:
        if not _KEY_RE.fullmatch(key):
            raise ValidationError("Idempotency-Key must match the published contract", field_name="Idempotency-Key")
        fingerprint = canonical_hash(request)
        receipt_key = (context.tenant_id, context.actor_id, operation, key)

        def transaction(state: StoreState) -> tuple[_Outcome, bool]:
            receipt = state.idempotency.get(receipt_key)
            if receipt:
                if receipt.request_sha256 != fingerprint:
                    raise ConflictError("Idempotency-Key was already used with a different payload", code="idempotency_conflict")
                return _Outcome(receipt.response_value, receipt.response_status, "replay", None, None, receipt.error), True
            outcome = mutate(state)
            self._audit(state, context, operation, outcome.aggregate_type, outcome.aggregate_id, outcome.aggregate_version, correlation_id,
                        outcome.error.code if outcome.error else "succeeded", outcome.error.code if outcome.error else None)
            state.idempotency[receipt_key] = IdempotencyReceipt(
                tenant_id=context.tenant_id, actor_id=context.actor_id, operation=operation, key=key, request_sha256=fingerprint,
                response_status=outcome.status, response_value=outcome.value, error=outcome.error, completed_at=self._clock(),
            )
            return outcome, False

        try:
            outcome, replayed = self.repository.atomic(transaction)
        except DomainError as exc:
            self._audit_failure(context, operation, correlation_id, exc.code)
            raise
        if outcome.error:
            raise outcome.error
        etag = self._etag(outcome.aggregate_version) if outcome.aggregate_version else None
        return ServiceResponse(outcome.value, outcome.status, etag, replayed)

    def _emit(self, state: StoreState, *, event_type: str, tenant_id: str, aggregate_type: str, aggregate_id: str, aggregate_version: int,
              correlation_id: str, idempotency_key: str, payload: dict[str, Any], now: datetime, causation_id: str | None = None) -> None:
        sequence = 1 + max(
            (event.sequence_no for (tenant, _), event in state.outbox.items()
             if tenant == tenant_id and event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id),
            default=0,
        )
        event = OutboxEvent(
            event_id=new_id(), event_type=event_type, tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
            aggregate_version=aggregate_version, sequence_no=sequence, correlation_id=correlation_id, causation_id=causation_id,
            idempotency_key=idempotency_key, classification="customer_confidential", payload=payload, occurred_at=now, emitted_at=now,
        )
        state.outbox[(tenant_id, event.event_id)] = event

    # ---- audit --------------------------------------------------------------

    def _audit(
        self, state: StoreState, context, action: str, aggregate_type: str, aggregate_id: str | None,
        aggregate_version: int | None, correlation_id: str, result: str, reason_code: str | None = None,
    ) -> None:
        state.audit_events.append(
            AuditEvent(
                id=new_id(), tenant_id=context.tenant_id, actor_id=context.actor_id, purpose=context.purpose,
                action=f"m02.{action}", aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                aggregate_version=aggregate_version, correlation_id=correlation_id, result=result,
                reason_code=reason_code, recorded_at=self._clock(),
            )
        )

    def _audit_failure(self, context, action: str, correlation_id: str | None, reason_code: str, aggregate_id: str | None = None) -> None:
        correlation = self._correlation(correlation_id)

        def record(state: StoreState) -> None:
            self._audit(state, context, action, "security" if aggregate_id is None else "contract", aggregate_id, None, correlation, "rejected", reason_code)

        self.repository.atomic(record)

    # ---- authorization and mapping helpers ---------------------------------

    def _authorize_staff(self, context, roles: set[str]) -> None:
        if context.principal_type == "staff" and context.roles & roles:
            return
        raise ForbiddenError("staff role is not allowed for this operation")

    def _authorize_customer_resource(self, context, customer_id: str, staff_roles: set[str]) -> None:
        if context.principal_type == "customer":
            if context.customer_id == customer_id:
                return
            raise NotFoundError("resource was not found")
        if context.principal_type == "staff" and context.roles & staff_roles:
            return
        if context.principal_type == "service" and "m02:*" in context.scopes:
            return
        raise ForbiddenError("actor cannot access this resource")

    @staticmethod
    def _body(body: dict[str, Any], allowed: set[str], required: set[str], min_properties: int = 0) -> None:
        if not isinstance(body, dict):
            raise ValidationError("request body must be an object")
        extra = set(body) - allowed
        missing = required - set(body)
        if extra:
            raise ValidationError(f"unsupported fields: {', '.join(sorted(extra))}", field_name=sorted(extra)[0])
        if missing:
            raise ValidationError(f"missing fields: {', '.join(sorted(missing))}", field_name=sorted(missing)[0])
        if len(body) < min_properties:
            raise ValidationError("request body has too few properties")

    @staticmethod
    def _validate_documents(documents: Any) -> list[dict[str, Any]]:
        if not isinstance(documents, list) or not 1 <= len(documents) <= 20:
            raise ValidationError("documents must contain 1 to 20 items", field_name="documents")
        required = {"asset_id", "sha256", "media_type", "classification", "scan_status"}
        cleaned = []
        for index, doc in enumerate(documents):
            if not isinstance(doc, dict) or set(doc) != required:
                raise ValidationError("document references must match the published shape", field_name=f"documents.{index}")
            require_uuid(doc["asset_id"], f"documents.{index}.asset_id")
            require_sha256(doc["sha256"], f"documents.{index}.sha256")
            if doc["media_type"] not in _MEDIA_TYPES:
                raise ValidationError("media_type is not supported", field_name=f"documents.{index}.media_type")
            if doc["classification"] not in _DOC_CLASSIFICATIONS:
                raise ValidationError("classification is not supported", field_name=f"documents.{index}.classification")
            if doc["scan_status"] != "clean":
                raise ValidationError("documents must be scanned clean before packaging", code="document_not_clean", field_name=f"documents.{index}.scan_status")
            cleaned.append(doc)
        return cleaned

    @staticmethod
    def _validate_service_selections(selections: Any) -> None:
        if not isinstance(selections, list) or not 1 <= len(selections) <= 10:
            raise ValidationError("service_selections must contain 1 to 10 items", field_name="service_selections")
        required = {"service_code", "offer_version", "quantity", "billing_interval", "quoted_price", "tax_mapping_version"}
        for index, item in enumerate(selections):
            if not isinstance(item, dict) or set(item) != required:
                raise ValidationError("service selections must match the published shape", field_name=f"service_selections.{index}")
            if item["billing_interval"] not in {"month", "year", "one_time"}:
                raise ValidationError("billing_interval is not supported", field_name=f"service_selections.{index}.billing_interval")
            if not isinstance(item["quantity"], int) or not 1 <= item["quantity"] <= 100:
                raise ValidationError("quantity must be between 1 and 100", field_name=f"service_selections.{index}.quantity")
            price = item["quoted_price"]
            if not isinstance(price, dict) or set(price) != {"currency", "amount"}:
                raise ValidationError("quoted_price must contain currency and amount", field_name=f"service_selections.{index}.quoted_price")
            if not re.fullmatch(r"[A-Z]{3}", str(price["currency"])) or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", str(price["amount"])):
                raise ValidationError("quoted_price has an invalid currency or decimal amount", field_name=f"service_selections.{index}.quoted_price")

    @staticmethod
    def _currency(selections: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
        return str(selections[0]["quoted_price"]["currency"]) if selections else "TWD"

    @staticmethod
    def _billing_period_end(starts_at: datetime, billing_interval: str) -> datetime:
        if billing_interval == "year":
            return starts_at + timedelta(days=365)
        if billing_interval == "month":
            return starts_at + timedelta(days=30)
        return starts_at + timedelta(days=1)

    @staticmethod
    def _compute_target(entitlement: Entitlement, service: ContractService, now: datetime, contract_status: str, payment_ref: str | None) -> str:
        if entitlement.status == "revoked":
            return "revoked"
        if entitlement.status == "suspended":
            return "suspended"
        if contract_status == "terminated":
            return "revoked"
        if now >= service.ends_at:
            return "expired"
        if now < service.starts_at:
            return "eligible" if contract_status in {"accepted", "effective"} else "locked"
        if contract_status not in {"accepted", "effective"}:
            return entitlement.status
        if payment_ref is None:
            return "eligible"
        return "active"

    @staticmethod
    def _transition_word(old: str, new: str, payment_ref: str | None) -> str:
        if new == old:
            if new == "suspended":
                return "deny"
            if new in {"locked", "eligible"} and payment_ref is None:
                return "deny"
            return "keep"
        if new == "active":
            return "grant"
        if new == "revoked":
            return "revoke"
        if new == "suspended":
            return "suspend"
        if new == "expired":
            return "expire"
        return "keep"

    @staticmethod
    def _cas(current: int, expected: int | None) -> None:
        if expected is None:
            raise PreconditionRequiredError("If-Match is required")
        if current != expected:
            raise StaleVersionError("resource version has changed", current_version=current)

    @staticmethod
    def _parse_time(value: str, field_name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return parsed.astimezone(timezone.utc)
        except (ValueError, AttributeError) as exc:
            raise ValidationError(f"{field_name} must be an RFC3339 timestamp", field_name=field_name) from exc

    @staticmethod
    def _correlation(value: str | None) -> str:
        return require_uuid(value, "X-Correlation-Id") if value else new_id()

    @staticmethod
    def _etag(version: int) -> str:
        return f'"{version}"'

    @staticmethod
    def _receipt(event_id: str, status: str, correlation_id: str) -> dict[str, Any]:
        return {"receipt_id": new_id(), "event_id": event_id, "status": status, "received_at": wire_time(utc_now()), "correlation_id": correlation_id}

    def _find_package(self, tenant_id: str, package_version_id: str) -> PackageVersion | None:
        return self.repository.read(lambda state: state.packages.get((tenant_id, package_version_id)))

    def _stored_package(self, state: StoreState, tenant_id: str, package_version_id: str) -> PackageVersion:
        package = state.packages.get((tenant_id, package_version_id))
        if not package:
            raise NotFoundError("contract package was not found")
        return package

    def _stored_contract(self, state: StoreState, tenant_id: str, contract_id: str) -> Contract:
        contract = state.contracts.get((tenant_id, contract_id))
        if not contract:
            raise NotFoundError("contract was not found")
        return contract

    def _stored_entitlement(self, state: StoreState, tenant_id: str, entitlement_id: str) -> Entitlement:
        entitlement = state.entitlements.get((tenant_id, entitlement_id))
        if not entitlement:
            raise NotFoundError("entitlement was not found")
        return entitlement

    def _package_outcome(self, package: PackageVersion, context, status: int) -> _Outcome:
        return _Outcome(package.to_api(self._package_actions(package, context)), status, "contract_package", package.id, package.version)

    def _package_actions(self, package: PackageVersion, context) -> list[str]:
        if package.state == "draft" and context.principal_type == "staff" and "compliance_reviewer" in context.roles and context.actor_id != package.proposed_by:
            return ["approve_contract_package"]
        return []

    def _contract_actions(self, contract: Contract, context) -> list[str]:
        if contract.status in {"terminated", "expired"}:
            return []
        actions = []
        if context.principal_type == "staff" and "contract_operator" in context.roles:
            actions.extend(["create_contract_amendment", "terminate_contract"])
        elif context.principal_type == "customer" and context.customer_id == contract.customer_id:
            actions.append("create_contract_amendment")
        return sorted(actions)

    def _entitlement_actions(self, entitlement: Entitlement, context) -> list[str]:
        if entitlement.status in {"active", "eligible"} and context.principal_type == "staff" and "contract_supervisor" in context.roles:
            return ["suspend_entitlement"]
        return []
