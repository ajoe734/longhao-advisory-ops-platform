import pytest

from modules.customer_identity_application.automation import DuplicateAutomationService, LocalDuplicateAssistant
from modules.customer_identity_application.domain import ActorContext, ConflictError, NotFoundError, ValidationError

from conftest import (
    MERGE_EVIDENCE,
    PROOF_A,
    REVIEW_EVIDENCE,
    SUPERVISOR,
    TENANT,
    create_customer,
)


def test_cross_tenant_staff_cannot_read_or_mutate_customer(m01, operator):
    customer = create_customer(m01["service"], operator, PROOF_A, "tenant-create-001").data
    other = ActorContext(
        "10000000-0000-0000-0000-000000000099",
        "20000000-0000-0000-0000-000000000099",
        frozenset({"cms_operator"}),
        principal_type="staff",
    )
    with pytest.raises(NotFoundError):
        m01["service"].get_customer(other, customer["id"])
    with pytest.raises(NotFoundError):
        m01["service"].update_customer(
            other, customer["id"], {"display_name": "跨租戶覆寫"},
            expected_version=1, idempotency_key="tenant-update-001",
        )
    assert m01["service"].get_customer(operator, customer["id"]).data["display_name"] == customer["display_name"]


def test_identity_correction_revokes_without_deleting_history_or_reauth(m01, operator, supervisor):
    customer = create_customer(m01["service"], operator, PROOF_A, "revoke-create-001").data
    before = m01["repository"].snapshot()
    identity = next(iter(before.identities.values()))
    revoked = m01["service"].revoke_external_identity(
        supervisor, customer["id"], identity.id,
        {"reason_code": "verified_mislink", "evidence_asset_ids": [REVIEW_EVIDENCE]},
        expected_version=1, idempotency_key="identity-revoke-001",
    )
    after = m01["repository"].snapshot()
    assert revoked.data["status"] == "revoked"
    assert len(after.identities) == 1
    assert after.identities[(TENANT, identity.id)].subject_hash == identity.subject_hash
    assert after.identity_index[(TENANT, identity.provider, identity.subject_hash)] == identity.id
    assert len(after.outbox) == len(before.outbox)  # no unregistered hidden event
    with pytest.raises(ConflictError) as denied:
        create_customer(m01["service"], operator, PROOF_A, "revoke-create-002")
    assert denied.value.code == "identity_revoked"


def test_minimized_automation_is_untrusted_and_cannot_merge(m01, operator):
    automation = DuplicateAutomationService(m01["repository"], LocalDuplicateAssistant())
    records = [
        {"customer_id": "60000000-0000-0000-0000-000000000001", "contact_fingerprints": ["a" * 64], "evidence_refs": [MERGE_EVIDENCE]},
        {"customer_id": "60000000-0000-0000-0000-000000000002", "contact_fingerprints": ["a" * 64], "evidence_refs": [REVIEW_EVIDENCE]},
    ]
    run = automation.suggest(operator, records)
    snapshot = m01["repository"].snapshot()
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert len(run.suggestions) == 1
    assert len(snapshot.merge_cases) == 0
    assert snapshot.automation_runs[(TENANT, run.id)] == run
    review = automation.record_review(
        operator, run.id, action="modified", reason_code="human_verified_pair",
        selected_customer_ids=(records[0]["customer_id"], records[1]["customer_id"]),
    )
    assert review.action == "modified"
    assert m01["repository"].snapshot().automation_reviews[(TENANT, review.id)] == review
    assert len(m01["repository"].snapshot().merge_cases) == 0
    with pytest.raises(ValidationError) as raw_pii:
        automation.suggest(operator, [{**records[0], "email": "forbidden@example.invalid"}])
    assert raw_pii.value.code == "automation_input_not_minimized"


def test_production_automation_refuses_local_fake(m01):
    with pytest.raises(RuntimeError):
        DuplicateAutomationService(m01["repository"], LocalDuplicateAssistant(), environment="production")
